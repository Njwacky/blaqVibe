"""Redis-free deploys: durable private uploads and shared atomic rate limits."""
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from threading import Event
from unittest.mock import patch

from django.core.cache import caches
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from blaqvibes.cache import DatabaseCache
from gallery.models import AppProject, CacheEntry, ScanJob
from gallery.scan_queue import enqueue_scan, process_database_job
from gallery.test_repo_import import fake_response, zip_bytes
from gallery.test_upload_failures import REMOTE_STORAGE
from gallery.tests import make_category, make_project, make_user, make_zip_file

DB_CACHES = {
    'default': {'BACKEND': 'blaqvibes.cache.DatabaseCache', 'LOCATION': 'default', 'KEY_PREFIX': 'test-ui'},
    'ratelimit': {'BACKEND': 'blaqvibes.cache.DatabaseCache', 'LOCATION': 'default', 'KEY_PREFIX': 'test-limits'},
}


class RedisFreeSettingsTests(SimpleTestCase):
    def test_production_defaults_ignore_a_leftover_redis_url(self):
        env = dict(os.environ, DEBUG='0', DJANGO_LOCAL_DEV='0', DJANGO_TEST='0',
                   SCAN_QUEUE_BACKEND='database', USE_REDIS='0', CELERY_EAGER='0',
                   REDIS_URL='redis://unreachable.invalid:6379/0', DATABASE_URL='', SUPABASE_URL='',
                   SECRET_KEY='test-only-settings-key-never-use-on-a-public-host-123456789')
        code = (
            "import json; from blaqvibes import settings as s; "
            "print(json.dumps([s.SCAN_QUEUE_BACKEND, s.CELERY_BROKER_URL, "
            "s.CELERY_RESULT_BACKEND, s.CACHES['ratelimit']['BACKEND']]))"
        )
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout.strip()),
                         ['database', 'memory://', None, 'blaqvibes.cache.DatabaseCache'])

    def test_main_requirements_do_not_require_redis(self):
        from pathlib import Path
        requirements = (Path(__file__).resolve().parent.parent / 'requirements.txt').read_text()
        self.assertNotIn('redis==', requirements)


@override_settings(CACHES=DB_CACHES)
class SharedDatabaseCacheTests(TestCase):
    def test_values_and_counters_are_shared_by_separate_instances(self):
        first = DatabaseCache('default', {'KEY_PREFIX': 'shared'})
        second = DatabaseCache('default', {'KEY_PREFIX': 'shared'})
        self.assertTrue(first.add('count', 1, 60))
        self.assertFalse(second.add('count', 1, 60))
        self.assertEqual(second.incr('count'), 2)
        self.assertEqual(first.get('count'), 2)
        first.set('payload', {'title': 'WhatsApp bot api', 'files': ['main.js']})
        self.assertEqual(second.get('payload')['files'], ['main.js'])

    def test_expired_keys_can_be_added_again_but_live_keys_are_not_reset(self):
        cache = caches['ratelimit']
        cache.add('count', 5, 60)
        self.assertFalse(cache.add('count', 1, 60))
        self.assertEqual(cache.get('count'), 5)
        CacheEntry.objects.update(expires=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(cache.get('count'))
        with self.assertRaises(ValueError):
            cache.incr('count')
        self.assertTrue(cache.add('count', 1, 60))
        self.assertEqual(cache.incr('count'), 2)

    def test_zero_and_forever_timeouts_and_many_operations(self):
        cache = caches['default']
        cache.set('expired', 'no', timeout=0)
        cache.set('forever', 'yes', timeout=None)
        self.assertEqual(cache.get_many(['expired', 'forever']), {'forever': 'yes'})
        cache.set_many({'one': 1, 'two': 2})
        self.assertEqual(cache.get_many(['one', 'two']), {'one': 1, 'two': 2})
        self.assertTrue(cache.touch('one', timeout=0))
        self.assertIsNone(cache.get('one'))
        cache.delete_many(['two', 'forever'])
        self.assertFalse(cache.get_many(['two', 'forever']))

    def test_clearing_ui_cache_does_not_clear_rate_limits(self):
        caches['default'].set('page', 'html')
        caches['ratelimit'].add('limit', 5, 60)
        caches['default'].clear()
        self.assertIsNone(caches['default'].get('page'))
        self.assertEqual(caches['ratelimit'].incr('limit'), 6)

    def test_cache_does_not_evict_live_rate_limits(self):
        cache = DatabaseCache('default', {'KEY_PREFIX': 'no-eviction', 'OPTIONS': {'MAX_ENTRIES': 1}})
        cache.add('limit', 5, 60)
        for i in range(110):
            cache.set(f'expired-{i}', 'expired', timeout=0)
        self.assertEqual(cache.incr('limit'), 6)

    def test_invalid_signature_is_never_unpickled(self):
        cache = caches['default']
        cache.set('value', {'private': 'data'})
        CacheEntry.objects.update(value='tampered:not-a-signature')
        with patch('blaqvibes.cache.pickle.loads', side_effect=AssertionError('must verify first')) as loads:
            self.assertEqual(cache.get('value', 'missing'), 'missing')
        loads.assert_not_called()

    def test_postgres_cache_table_has_rls(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Supabase/Postgres policy test')
        with connection.cursor() as cursor:
            cursor.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'blaqvibes_cache'::regclass")
            self.assertTrue(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM pg_policies WHERE tablename = 'blaqvibes_cache'")
            self.assertEqual(cursor.fetchone()[0], 0)


    def test_postgres_api_reader_cannot_read_private_cache_payloads(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Supabase/Postgres policy test')
        with connection.cursor() as cursor:
            cursor.execute('SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user')
            if not cursor.fetchone()[0]:
                self.skipTest('Test database role cannot create an isolated API stand-in')
            import uuid
            role = connection.ops.quote_name('cache_reader_' + uuid.uuid4().hex[:16])
            # Role and grants are rolled back with this TestCase transaction.
            cursor.execute(f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO {role}')
            cursor.execute(f'GRANT SELECT ON blaqvibes_cache TO {role}')
            caches['default'].set('private', {'not_for_the_api': True})
            cursor.execute('SELECT COUNT(*) FROM blaqvibes_cache')
            self.assertGreater(cursor.fetchone()[0], 0)
            try:
                cursor.execute(f'SET LOCAL ROLE {role}')
                cursor.execute('SELECT COUNT(*) FROM blaqvibes_cache')
                self.assertEqual(cursor.fetchone()[0], 0)
            finally:
                cursor.execute('RESET ROLE')


class AtomicDatabaseCounterTests(TransactionTestCase):
    def test_concurrent_postgres_increments_never_lose_requests(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Production multi-worker counter test needs Postgres')
        cache = DatabaseCache('default', {'KEY_PREFIX': 'concurrent'})
        cache.add('counter', 0, 60)

        def increment(_):
            try:
                own_cache = DatabaseCache('default', {'KEY_PREFIX': 'concurrent'})
                return [own_cache.incr('counter') for _ in range(10)]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(increment, range(8)))
        self.assertEqual(cache.get('counter'), 80)
        self.assertEqual(sorted(n for batch in results for n in batch), list(range(1, 81)))

    def test_concurrent_postgres_add_has_one_winner(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Production multi-worker counter test needs Postgres')

        def add(_):
            try:
                return DatabaseCache('default', {'KEY_PREFIX': 'add-race'}).add('counter', 1, 60)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(add, range(8))), 1)


    def test_concurrent_postgres_add_replaces_an_expired_key_once(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Production multi-worker counter test needs Postgres')
        cache = DatabaseCache('default', {'KEY_PREFIX': 'expired-race'})
        cache.add('counter', 99, 0)
        def add(_):
            try:
                return DatabaseCache('default', {'KEY_PREFIX': 'expired-race'}).add('counter', 1, 60)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(add, range(8))), 1)
        self.assertEqual(cache.get('counter'), 1)


@override_settings(SCAN_QUEUE_BACKEND='database', CELERY_TASK_ALWAYS_EAGER=False,
                   STORAGES=REMOTE_STORAGE, RATELIMIT_ENABLE=False)
class RedisFreeUploadTests(TestCase):
    def setUp(self):
        self.category = make_category()
        self.user = make_user('redis-free-builder')
        self.client.force_login(self.user)

    def test_zip_and_github_uploads_never_contact_a_broker_or_scan_inline(self):
        for source in ('zip', 'github'):
            with self.subTest(source=source), patch('kombu.Connection.connect', side_effect=AssertionError('no broker')), \
                    patch('gallery.tasks.process_upload_pipeline.apply_async', side_effect=AssertionError('no async')), \
                    patch('gallery.tasks.scan_zip_with_clamav.apply', side_effect=AssertionError('no HTTP scans')):
                if source == 'zip':
                    response = self.client.post('/publish/', {
                        'title': 'WhatsApp bot api', 'short_description': 'A bot API project.',
                        'build_method': 'human', 'zip_file': make_zip_file({'main.js': 'console.log(1);'}),
                    })
                else:
                    with patch('gallery.repo_import.requests.get', return_value=fake_response(
                            zip_bytes({'demo-main/main.js': 'console.log(1);'}))):
                        response = self.client.post('/import/github/', {'repo_url': 'https://github.com/owner/demo'})
            self.assertEqual(response.status_code, 302)
            project = AppProject.objects.filter(owner=self.user).latest('pk')
            self.assertEqual(project.status, 'pending')
            self.assertTrue(project.zip_file.storage.exists(project.zip_file.name))
            self.assertEqual(project.scan_job.status, 'queued')
            self.assertEqual(project.scan_job.task_id, '')
            self.client.logout()
            self.assertEqual(self.client.get(project.get_absolute_url()).status_code, 404)
            self.client.force_login(self.user)
            page = self.client.get(response['Location'])
            self.assertNotContains(page, 'temporarily unavailable')
            if source == 'zip':
                self.assertContains(page, 'waiting for safety checks')
                self.assertNotContains(page, 'safety scan running')
            self.assertRegex(response['X-Upload-Reference'], r'^[a-f0-9]{32}$')

    @override_settings(CACHES=DB_CACHES, RATELIMIT_ENABLE=True, RATELIMIT_USE_CACHE='ratelimit')
    def test_upload_rate_limit_still_blocks_the_sixth_request(self):
        for i in range(6):
            if i == 5:
                caches['default'].clear()  # UI invalidation must not reset limits.
            response = self.client.post('/publish/', {
                'title': f'Bot project {i}', 'short_description': 'A bot API project.',
                'build_method': 'human', 'zip_file': make_zip_file({'main.js': 'console.log(1);'}),
            })
            self.assertEqual(response.status_code, 429 if i == 5 else 302)
        self.assertEqual(AppProject.objects.filter(owner=self.user).count(), 5)

    def test_database_health_does_not_ping_redis(self):
        with patch('gallery.health._ping_redis', side_effect=AssertionError('Redis must be optional')) as ping:
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['checks']['queue'],
                         {'ok': True, 'detail': 'database_queue', 'backend': 'database'})
        ping.assert_not_called()


@override_settings(SCAN_QUEUE_BACKEND='database', CELERY_TASK_ALWAYS_EAGER=False,
                   STORAGES=REMOTE_STORAGE, RATELIMIT_ENABLE=False)
class DatabaseScanRunnerTests(TestCase):
    def setUp(self):
        self.category = make_category()
        self.user = make_user('scanner-builder')
        make_user('scanner-moderator', role='moderator')
        # Existing publication policy: experienced authors can auto-publish
        # after real scans, new accounts still require a moderator.
        for i in range(3):
            make_project(self.user, self.category, title=f'Prior checked build {i}')
        self.vuln = patch('gallery.tasks.vulnerability_scan.run')
        self.vuln.start()
        self.addCleanup(self.vuln.stop)
        self.attention = patch('gallery.tasks.attention_check.run')
        self.attention.start()
        self.addCleanup(self.attention.stop)

    def queued(self, title='Saved bot', files=None, owner=None):
        project = make_project(owner or self.user, self.category, title=title, status='pending')
        project.zip_file.save('app.zip', make_zip_file(files or {'main.js': 'console.log(1);'}), save=True)
        enqueue_scan(project)
        return project.scan_job

    def run_with_clamav(self, job, exit_code=0):
        with patch('gallery.tasks.subprocess.run', return_value=SimpleNamespace(returncode=exit_code)), \
                patch('kombu.Connection.connect', side_effect=AssertionError('no broker')):
            result = process_database_job(job.pk)
        job.refresh_from_db()
        job.project.refresh_from_db()
        return result

    def test_runner_checks_saved_bytes_and_can_publish_without_redis(self):
        job = self.queued()
        self.assertEqual(self.run_with_clamav(job), 'processed')
        self.assertEqual(job.status, 'clean')
        self.assertEqual(job.project.status, 'published')
        self.assertEqual(job.project.scan_report['clamav'], 'clean')
        self.assertTrue(job.task_id.startswith('database:'))

    def test_post_scan_attention_failure_does_not_unpublish_a_clean_job(self):
        job = self.queued()
        with patch('gallery.tasks.attention_check.run', side_effect=RuntimeError('optional attention failed')):
            self.assertEqual(self.run_with_clamav(job), 'processed')
        self.assertEqual(job.status, 'clean')
        self.assertEqual(job.project.status, 'published')

    def test_new_author_still_requires_review(self):
        job = self.queued(owner=make_user('new-bot-builder'))
        self.assertEqual(self.run_with_clamav(job), 'processed')
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.project.status, 'pending')
        self.client.force_login(job.project.owner)
        page = self.client.get(reverse('publish_success', args=[job.project.slug]))
        self.assertContains(page, 'awaiting review')
        self.assertNotContains(page, 'safety scan running')
        self.assertNotContains(page, ' — published — ')
        from gallery.zip_serve import scan_progress
        progress = scan_progress(job.project)
        self.assertTrue(progress['held'])
        self.assertIn('moderator', progress['headline'])
        for step in progress['steps']:
            self.assertNotEqual(step['state'], 'active')
        self.assertEqual({s['key']: s['state'] for s in progress['steps']}['virus'], 'done')
        self.assertEqual({s['key']: s['state'] for s in progress['steps']}['secrets'], 'done')

    def test_virus_never_publishes(self):
        job = self.queued()
        self.assertEqual(self.run_with_clamav(job, exit_code=1), 'processed')
        self.assertEqual(job.status, 'quarantined')
        self.assertEqual(job.project.status, 'quarantined')

    def test_clamav_error_exit_is_not_mistaken_for_clean(self):
        job = self.queued()
        self.assertEqual(self.run_with_clamav(job, exit_code=2), 'processed')
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.project.status, 'pending')
        self.assertEqual(job.project.scan_report['clamav'], 'unavailable')

    def test_missing_scanner_never_publishes(self):
        job = self.queued()
        with patch('gallery.tasks.subprocess.run', side_effect=FileNotFoundError('not installed')):
            self.assertEqual(process_database_job(job.pk), 'processed')
        job.project.refresh_from_db()
        self.assertEqual(job.project.status, 'pending')
        self.assertEqual(job.project.scan_report['clamav'], 'unavailable')

    def test_secret_never_auto_publishes(self):
        job = self.queued(files={'main.js': 'const example = "sk_live_NOTAREALKEY";'})
        self.run_with_clamav(job)
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.project.status, 'pending')
        self.assertTrue(job.project.scan_report['secrets'])

    def test_real_dependency_step_does_not_erase_secret_flags(self):
        self.vuln.stop()
        job = self.queued(files={'main.js': 'const example = "sk_live_NOTAREALKEY";'})
        with patch('gallery.nolo_review.nolo_review', return_value={'score': 8, 'source': 'test'}):
            self.assertEqual(self.run_with_clamav(job), 'processed')
        self.assertEqual(job.status, 'pending')
        self.assertEqual(job.project.status, 'pending')
        self.assertEqual(job.project.scan_report['secrets'], ['main.js'])
        self.assertNotIn('sk_live_NOTAREALKEY', str(job.project.scan_report))

    def test_unreadable_secret_scan_is_a_failure_not_a_clean_verdict(self):
        job = self.queued()
        with patch('gallery.ziputil.open_zip', side_effect=OSError('private storage failure')):
            self.assertEqual(self.run_with_clamav(job), 'failed')
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.project.status, 'pending')

    def test_removal_during_a_virus_scan_cannot_be_undone(self):
        for exit_code in (0, 1, 2):
            with self.subTest(exit_code=exit_code):
                job = self.queued(title=f'Removed build {exit_code}')
                def removed(*args, **kwargs):
                    AppProject.objects.filter(pk=job.project_id).update(status='removed')
                    return SimpleNamespace(returncode=exit_code)
                with patch('gallery.tasks.subprocess.run', side_effect=removed):
                    self.assertEqual(process_database_job(job.pk), 'skipped')
                job.project.refresh_from_db()
                self.assertEqual(job.project.status, 'removed')
                self.assertFalse(job.project.scan_report)

    def test_old_virus_verdict_cannot_quarantine_replacement_bytes(self):
        job = self.queued()
        def replacement(*args, **kwargs):
            project = AppProject.objects.get(pk=job.project_id)
            project.zip_file.save('replacement.zip', make_zip_file({'main.js': 'new unchecked bytes'}), save=True)
            project.scan_report = {}
            project.save(update_fields=['scan_report'])
            enqueue_scan(project)
            return SimpleNamespace(returncode=1)
        with patch('gallery.tasks.subprocess.run', side_effect=replacement):
            self.assertEqual(process_database_job(job.pk), 'skipped')
        job.refresh_from_db()
        job.project.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.task_id, '')
        self.assertEqual(job.project.status, 'pending')
        self.assertFalse(job.project.scan_report)

    def test_old_dependency_evidence_cannot_overwrite_a_replacement(self):
        self.vuln.stop()
        job = self.queued()
        def replacement(*args, **kwargs):
            project = AppProject.objects.get(pk=job.project_id)
            project.zip_file.save('replacement.zip', make_zip_file({'main.js': 'new unchecked bytes'}), save=True)
            project.scan_report = {'replacement': True}
            project.save(update_fields=['scan_report'])
            enqueue_scan(project)
            return {'score': 8, 'source': 'old-check'}
        with patch('gallery.nolo_review.nolo_review', side_effect=replacement):
            self.assertEqual(self.run_with_clamav(job), 'skipped')
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.project.status, 'pending')
        self.assertEqual(job.project.scan_report, {'replacement': True})

    def test_changed_snippet_is_requeued_instead_of_auto_published(self):
        project = make_project(self.user, self.category, title='Snippet to check', status='pending', html_code='<p>old</p>')
        enqueue_scan(project)
        job = project.scan_job
        with patch('gallery.tasks.vulnerability_scan.run', side_effect=lambda *a, **k:
                   AppProject.objects.filter(pk=project.pk).update(html_code='<p>new unchecked</p>')):
            self.assertEqual(process_database_job(job.pk), 'skipped')
        job.refresh_from_db()
        project.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.task_id, '')
        self.assertEqual(project.status, 'pending')

    def test_already_claimed_job_is_not_processed_twice(self):
        job = self.queued()
        ScanJob.objects.filter(pk=job.pk).update(status='scanning', task_id='database:other-runner')
        with patch('gallery.tasks.scan_zip_with_clamav.apply') as scanner:
            self.assertEqual(process_database_job(job.pk), 'skipped')
        scanner.assert_not_called()

    def test_changed_archive_is_not_published_by_an_old_claim(self):
        job = self.queued()

        def replacement(*args, **kwargs):
            project = AppProject.objects.get(pk=job.project_id)
            project.zip_file.save('replacement.zip', make_zip_file({'main.js': 'unchecked new bytes'}), save=True)
            project.scan_report = {}
            project.save(update_fields=['scan_report'])
            enqueue_scan(project)

        with patch('gallery.tasks.vulnerability_scan.run', side_effect=replacement):
            self.assertEqual(self.run_with_clamav(job), 'skipped')
        self.assertEqual(job.project.status, 'pending')
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.task_id, '')

    def test_removed_project_is_not_resurrected(self):
        job = self.queued()
        with patch('gallery.tasks.vulnerability_scan.run', side_effect=lambda *a, **k:
                   AppProject.objects.filter(pk=job.project_id).update(status='removed')):
            self.assertEqual(self.run_with_clamav(job), 'skipped')
        self.assertEqual(job.project.status, 'removed')

    def test_runner_failure_is_durable_and_command_reports_it(self):
        job = self.queued()
        with patch('gallery.tasks.scan_zip_with_clamav.apply', side_effect=RuntimeError('private failure')):
            with self.assertRaises(CommandError):
                call_command('process_scan_queue', stdout=io.StringIO())
        job.refresh_from_db()
        job.project.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.project.status, 'pending')
        self.assertTrue(job.project.zip_file.storage.exists(job.project.zip_file.name))

    def test_recovery_requeues_saved_failed_jobs_without_duplication(self):
        job = self.queued()
        ScanJob.objects.filter(pk=job.pk).update(status='failed', task_id='old-celery-task')
        before = AppProject.objects.count()
        with patch('gallery.tasks.process_upload_pipeline.apply_async', side_effect=AssertionError('no broker')):
            call_command('retry_failed_scans', stdout=io.StringIO())
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.task_id, '')
        self.assertEqual(AppProject.objects.count(), before)

    def test_stale_database_claim_recovery_requires_opt_in(self):
        job = self.queued()
        ScanJob.objects.filter(pk=job.pk).update(
            status='scanning', task_id='database:crashed-runner',
            updated_at=timezone.now() - timedelta(minutes=31),
        )
        call_command('retry_failed_scans', stdout=io.StringIO())
        job.refresh_from_db()
        self.assertEqual(job.status, 'scanning')
        call_command('retry_failed_scans', include_stale=True, stdout=io.StringIO())
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.task_id, '')

    def test_batch_limit_and_backend_validation(self):
        first = self.queued(title='First bot')
        second = self.queued(title='Second bot')
        with patch('gallery.tasks.subprocess.run', return_value=SimpleNamespace(returncode=0)):
            call_command('process_scan_queue', limit=1, stdout=io.StringIO())
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, 'clean')
        self.assertEqual(second.status, 'queued')
        for limit in (0, 1001):
            with self.assertRaises(CommandError):
                call_command('process_scan_queue', limit=limit, stdout=io.StringIO())
        with override_settings(SCAN_QUEUE_BACKEND='celery'), self.assertRaises(CommandError):
            call_command('process_scan_queue', stdout=io.StringIO())


@override_settings(SCAN_QUEUE_BACKEND='database', STORAGES=REMOTE_STORAGE)
class ConcurrentScanClaimTests(TransactionTestCase):
    def test_two_postgres_runners_cannot_scan_the_same_claim(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Production worker claim test needs Postgres')
        project = make_project(make_user('claim-builder'), make_category(), status='pending')
        project.zip_file.save('app.zip', make_zip_file({'main.js': 'console.log(1);'}), save=True)
        enqueue_scan(project)
        started, release = Event(), Event()
        def scanner(*args, **kwargs):
            started.set()
            if not release.wait(10):
                raise RuntimeError('test worker wait timed out')
        def run():
            try:
                return process_database_job(project.scan_job.pk)
            finally:
                connections.close_all()
        with patch('gallery.tasks.scan_zip_with_clamav.apply', side_effect=scanner) as scan, \
                patch('gallery.tasks.vulnerability_scan.apply'), \
                patch('gallery.tasks.finalize_publish.apply'), \
                patch('gallery.tasks.attention_check.apply'), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run)
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(pool.submit(run).result(timeout=5), 'skipped')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5), 'processed')
        self.assertEqual(scan.call_count, 1)
