"""Deployment-only failures: object-store errors and an unavailable scan queue.

The ordinary suite runs eager Celery on local disk. Exercise the HTTP views
with a pathless storage backend and a failing publisher too: otherwise a
30-second Gunicorn abort is invisible to Django's test client.
"""
import io
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import models
from django.utils import timezone
from django.test import TestCase, override_settings
from kombu.exceptions import OperationalError

from gallery.models import AppFile, AppProject, Notification, ScanJob
from gallery.test_repo_import import fake_response, zip_bytes
from gallery.tests import make_category, make_project, make_user, make_zip_file


REMOTE_STORAGE = {
    'default': {'BACKEND': 'gallery.test_ziputil.InMemoryRemoteStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


@override_settings(RATELIMIT_ENABLE=False, STORAGES=REMOTE_STORAGE,
                   CELERY_TASK_ALWAYS_EAGER=False)
class UploadFailureTests(TestCase):
    def setUp(self):
        self.category = make_category()
        self.user = make_user('upload-builder')
        self.client.force_login(self.user)
        self.github = patch(
            'gallery.repo_import.requests.get',
            return_value=fake_response(zip_bytes({'demo-main/main.py': 'print(1)\n'})),
        )
        self.github.start()
        self.addCleanup(self.github.stop)

    def post(self, source='zip'):
        if source == 'github':
            return self.client.post('/import/github/', {
                'repo_url': 'https://github.com/owner/demo', 'title': 'My imported vibe',
            })
        upload = make_zip_file({'main.py': 'print(1)\n'})
        self.upload_bytes = upload.read()
        upload.seek(0)
        return self.client.post('/publish/', {
            'title': 'My uploaded vibe',
            'short_description': 'A tiny app to exercise the production upload path.',
            'build_method': 'human',
            'publish_token': 'failure-probe',
            'zip_file': upload,
        })

    def test_github_storage_failure_is_503_with_the_link_and_title_preserved(self):
        error = ClientError({'Error': {'Code': 'AccessDenied',
                                     'Message': 'private-bucket-do-not-display'}}, 'PutObject')
        self.client.raise_request_exception = False
        with patch('gallery.test_ziputil.InMemoryRemoteStorage._save', side_effect=error):
            response = self.post('github')
        self.assertEqual(response.status_code, 503)
        body = response.content.decode()
        self.assertIn('couldn’t save', body)
        self.assertIn('https://github.com/owner/demo', body)
        self.assertIn('My imported vibe', body)
        self.assertNotIn('private-bucket-do-not-display', body)
        self.assertEqual(AppProject.objects.count(), 0)
        self.assertEqual(ScanJob.objects.count(), 0)
        self.assertRegex(response['X-Upload-Reference'], r'^[a-f0-9]{32}$')
        self.assertIn(response['X-Upload-Reference'], body)

    def test_zip_storage_failure_identifies_the_exact_stage_in_the_logs(self):
        with self.assertLogs('gallery.upload_diagnostics', level='INFO') as logs:
            with patch('gallery.test_ziputil.InMemoryRemoteStorage._save',
                       side_effect=OSError('private-storage-detail')):
                response = self.post()
        self.assertEqual(response.status_code, 503)
        reference = response['X-Upload-Reference']
        trace = '\n'.join(logs.output)
        self.assertIn(f'ref={reference}', trace)
        self.assertIn('stage=save_project event=failed', trace)
        self.assertIn('OSError: private-storage-detail', trace)
        self.assertNotIn('private-storage-detail', response.content.decode())
        self.assertEqual(AppProject.objects.count(), 0)

    def test_zip_queue_failure_never_runs_the_scanner_in_the_web_request(self):
        self.check_queue_failure('zip')

    def test_github_queue_failure_uses_the_same_safe_path(self):
        self.check_queue_failure('github')

    def check_queue_failure(self, source):
        with patch('gallery.tasks.process_upload_pipeline.apply_async',
                   side_effect=OperationalError('private-redis-detail')) as publish:
            with patch('gallery.tasks.scan_zip_with_clamav.apply',
                       side_effect=AssertionError('Do not scan inside an HTTP request')) as scanner:
                response = self.post(source)
        self.assertEqual(response.status_code, 302)
        scanner.assert_not_called()
        self.assertFalse(publish.call_args.kwargs['retry'])
        self.assertTrue(publish.call_args.kwargs['ignore_result'])
        connection = publish.call_args.kwargs['connection']
        self.assertEqual(connection.transport_options['max_retries'], 0)
        self.assertEqual(connection.transport_options['connect_retries_timeout'], 3)
        self.assertEqual(connection.connect_timeout, 3)
        project = AppProject.objects.get(owner=self.user)
        self.assertEqual(project.status, 'pending')
        self.assertTrue(project.zip_file.storage.exists(project.zip_file.name))
        self.assertEqual(ScanJob.objects.get(project=project).status, 'failed')
        self.assertTrue(Notification.objects.filter(user=self.user, kind='review_needed').exists())
        page = self.client.get(response['Location'])
        body = page.content.decode()
        self.assertIn('files are saved', body)
        self.assertNotIn('private-redis-detail', body)
        self.assertNotIn('safety scan running', body)
        self.assertNotIn('#0 in line', body)
        self.assertFalse(AppProject.objects.filter(status='published').exists())

    def test_successful_remote_upload_keeps_the_original_bytes(self):
        with patch('gallery.tasks.process_upload_pipeline.apply_async',
                   return_value=SimpleNamespace(id='scan-task')) as publish:
            response = self.post()
        self.assertEqual(response.status_code, 302)
        project = AppProject.objects.get(owner=self.user)
        self.assertEqual(project.language_stats, {'Python': 100})
        self.assertEqual(project.zip_file.storage.blobs[project.zip_file.name], self.upload_bytes)
        self.assertEqual(ScanJob.objects.get(project=project).task_id, 'scan-task')
        self.assertFalse(publish.call_args.kwargs['retry'])
        self.assertTrue(publish.call_args.kwargs['ignore_result'])

    def test_file_index_uses_bulk_inserts_not_one_remote_round_trip_per_file(self):
        with patch('gallery.tasks.process_upload_pipeline.apply_async',
                   return_value=SimpleNamespace(id='scan-task')):
            with patch.object(AppFile.objects, 'create',
                              side_effect=AssertionError('Do not insert one file at a time')):
                response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(AppFile.objects.values_list('path', flat=True)), ['main.py'])

    def test_zip_database_insert_failure_does_not_break_the_form_rerender(self):
        self.check_database_failure('zip')

    def test_github_database_insert_failure_does_not_break_the_form_rerender(self):
        self.check_database_failure('github')

    def check_database_failure(self, source):
        existing = make_project(self.user, self.category, title='Existing build')

        def duplicate_slug(project, *args, **kwargs):
            project.slug = existing.slug
            # Cause a REAL database IntegrityError after FileField.pre_save.
            # Catching it without a savepoint poisons the connection, and the
            # form/template's next DB read becomes another unhandled 500.
            return models.Model.save(project, *args, **kwargs)

        with patch.object(AppProject, 'save', autospec=True, side_effect=duplicate_slug):
            response = self.post(source)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(AppProject.objects.count(), 1)
        self.assertEqual(ScanJob.objects.count(), 0)
        self.assertIn('nothing was published', response.content.decode())

    def test_fast_worker_verdict_is_not_overwritten_by_the_http_request(self):
        def finish_scan(*args, **kwargs):
            ScanJob.objects.update(status='pending')
            return SimpleNamespace(id='finished-task')

        with patch('gallery.tasks.process_upload_pipeline.apply_async', side_effect=finish_scan):
            response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ScanJob.objects.get().status, 'pending')
        self.assertEqual(ScanJob.objects.get().task_id, 'finished-task')

    def test_successful_upload_logs_each_stage_under_one_reference(self):
        with self.assertLogs('gallery.upload_diagnostics', level='INFO') as logs:
            with patch('gallery.tasks.process_upload_pipeline.apply_async',
                       return_value=SimpleNamespace(id='scan-task')):
                response = self.post()
        reference = response['X-Upload-Reference']
        trace = '\n'.join(logs.output)
        for stage in ('validate_form', 'save_project', 'build_tree', 'queue_scan'):
            self.assertIn(f'stage={stage} event=started', trace)
            self.assertIn(f'stage={stage} event=completed', trace)
        self.assertTrue(all(f'ref={reference}' in line for line in logs.output))
        self.assertIn('elapsed_ms=', trace)

    def test_github_download_failure_identifies_fetch_archive_not_storage(self):
        with self.assertLogs('gallery.upload_diagnostics', level='INFO') as logs:
            with patch('gallery.repo_import.requests.get', side_effect=RuntimeError('upstream probe')):
                response = self.post('github')
        self.assertEqual(response.status_code, 200)
        trace = '\n'.join(logs.output)
        self.assertIn('stage=fetch_archive event=failed', trace)
        self.assertNotIn('stage=save_project', trace)
        self.assertEqual(AppProject.objects.count(), 0)


@override_settings(RATELIMIT_ENABLE=False, STORAGES=REMOTE_STORAGE,
                   CELERY_TASK_ALWAYS_EAGER=False)
class FailedScanRecoveryTests(TestCase):
    def setUp(self):
        self.category = make_category()
        self.user = make_user('scan-recovery')

    def failed_job(self, status='pending', title='Saved build'):
        project = make_project(self.user, self.category, status=status, title=title)
        project.zip_file.save('app.zip', make_zip_file({'main.py': 'print(1)\n'}), save=True)
        return ScanJob.objects.create(project=project, status='failed')

    def test_recovery_requeues_saved_files_without_creating_another_vibe(self):
        job = self.failed_job()
        before = AppProject.objects.count()
        with patch('gallery.tasks.process_upload_pipeline.apply_async',
                   return_value=SimpleNamespace(id='recovered-task')) as publish:
            call_command('retry_failed_scans', stdout=io.StringIO())
        publish.assert_called_once()
        self.assertEqual(publish.call_args.kwargs['args'], [job.project_id])
        job.refresh_from_db()
        self.assertEqual(job.status, 'scanning')
        self.assertEqual(job.task_id, 'recovered-task')
        self.assertEqual(AppProject.objects.count(), before)
        job.project.refresh_from_db()
        self.assertEqual(job.project.status, 'pending')

    def test_recovery_never_requeues_removed_quarantined_or_published_projects(self):
        for status in ('removed', 'quarantined', 'published'):
            self.failed_job(status=status, title=f'{status} build')
        with patch('gallery.tasks.process_upload_pipeline.apply_async') as publish:
            call_command('retry_failed_scans', stdout=io.StringIO())
        publish.assert_not_called()

    def test_recovery_stops_on_a_dead_broker_and_retains_the_failed_job(self):
        job = self.failed_job()
        self.failed_job(title='Second saved build')
        with patch('gallery.tasks.process_upload_pipeline.apply_async',
                   side_effect=OperationalError('offline')) as publish:
            with self.assertRaises(CommandError):
                call_command('retry_failed_scans', stdout=io.StringIO())
        publish.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(ScanJob.objects.filter(status='failed').count(), 2)

    def test_stale_unsent_jobs_require_opt_in_and_sent_jobs_are_never_requeued(self):
        unsent = self.failed_job(title='Pre-fix timeout')
        sent = self.failed_job(title='Already sent')
        ScanJob.objects.filter(pk=unsent.pk).update(
            status='queued', updated_at=timezone.now() - timedelta(minutes=6),
        )
        ScanJob.objects.filter(pk=sent.pk).update(
            status='queued', task_id='real-task', updated_at=timezone.now() - timedelta(minutes=6),
        )
        with patch('gallery.tasks.process_upload_pipeline.apply_async',
                   return_value=SimpleNamespace(id='recovered-task')) as publish:
            call_command('retry_failed_scans', stdout=io.StringIO())
            publish.assert_not_called()
            call_command('retry_failed_scans', include_stale=True, stdout=io.StringIO())
        publish.assert_called_once()
        self.assertEqual(publish.call_args.kwargs['args'], [unsent.project_id])
        sent.refresh_from_db()
        self.assertEqual(sent.task_id, 'real-task')
