"""Waiting-for-approval: inbox note + file receipt + honest hold copy.

The post-upload page used to pulse with no explanation and no inbox row,
so people thought it had frozen. These tests pin the contract:
- a pending upload writes a queued notification that names the file and bytes
- the detail page says why it is waiting and shows the receipt
- /scan-status/ tells the owner the phase (scanning vs human hold)
- a moderator approve writes a published notification
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from gallery.models import AppProject, Notification, ScanJob
from gallery.pending import display_file_name, format_bytes, notify_queued, upload_receipt
from gallery.tests import make_category, make_project, make_user, make_zip_file


class FormatBytesTests(SimpleTestCase):
    def test_raw_bytes_under_a_kilobyte(self):
        self.assertEqual(format_bytes(0), '0 bytes')
        self.assertEqual(format_bytes(512), '512 bytes')

    def test_includes_raw_count_above_a_kilobyte(self):
        label = format_bytes(2048)
        self.assertIn('2.0 KB', label)
        self.assertIn('2,048 bytes', label)

    def test_garbage_becomes_zero(self):
        self.assertEqual(format_bytes(None), '0 bytes')
        self.assertEqual(format_bytes('nope'), '0 bytes')

    def test_storage_suffix_is_stripped_for_display(self):
        self.assertEqual(display_file_name('apps/zips/cool-app_OMnG3Mm.zip'), 'cool-app.zip')
        self.assertEqual(display_file_name('cool-app.zip'), 'cool-app.zip')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class PendingApprovalTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('waitowner')
        self.client.force_login(self.owner)

    def _snippet_payload(self, title='Held snippet'):
        return {
            'title': title,
            'category': self.cat.id,
            'short_description': 'A tiny snippet waiting for a human.',
            'readme': '# Held\n\n' + ('Enough characters in this readme body. ' * 6),
            'tech_stack': 'HTML',
            'html_code': '<p>hello pending</p>',
            'css_code': 'p{color:red}',
            'js_code': 'console.log(1)',
            'star_cost': 0,
            'price_zar': 0,
            'creator_kind': '',
        }

    def test_new_creator_snippet_writes_queued_inbox_with_bytes(self):
        response = self.client.post('/publish/', self._snippet_payload(), follow=True)
        self.assertEqual(response.status_code, 200)
        project = AppProject.objects.get(title='Held snippet')
        self.assertEqual(project.status, 'pending')
        note = Notification.objects.filter(user=self.owner, kind='queued').first()
        self.assertIsNotNone(note)
        self.assertIn('waiting for approval', note.title.lower())
        self.assertIn('bytes', note.body.lower())
        self.assertIn(project.get_absolute_url(), note.url)

    def test_auto_published_snippet_does_not_write_queued(self):
        for i in range(3):
            make_project(self.owner, self.cat, title=f'Prior live {i}')
        self.client.post('/publish/', self._snippet_payload(title='Instant snippet'), follow=True)
        project = AppProject.objects.get(title='Instant snippet')
        self.assertEqual(project.status, 'published')
        self.assertFalse(Notification.objects.filter(user=self.owner, kind='queued').exists())

    def test_zip_upload_inbox_names_the_file(self):
        payload = self._snippet_payload(title='Zip wait')
        payload['html_code'] = ''
        payload['css_code'] = ''
        payload['js_code'] = ''
        zip_bytes = make_zip_file({'app.py': 'print(1)\n', 'README.md': '# hi\n'}, name='cool-app.zip')
        fake_task = mock.Mock()
        fake_task.id = 'task-wait-1'
        with mock.patch('gallery.tasks.process_upload_pipeline.delay', return_value=fake_task):
            response = self.client.post(
                '/publish/',
                {**payload, 'zip_file': zip_bytes},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        project = AppProject.objects.get(title='Zip wait')
        self.assertEqual(project.status, 'pending')
        note = Notification.objects.get(user=self.owner, kind='queued')
        self.assertIn('cool-app', note.body)
        self.assertIn('bytes', note.body.lower())

    def test_detail_page_explains_the_hold_and_shows_receipt(self):
        project = make_project(self.owner, self.cat, title='Waiting vibe', status='pending')
        project.zip_file.save('cool-app.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        ScanJob.objects.create(project=project, status='scanning')
        response = self.client.get(project.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Waiting for approval')
        self.assertContains(response, 'not stuck')
        self.assertContains(response, 'cool-app.zip')
        self.assertContains(response, 'bytes')
        self.assertContains(response, 'Open Inbox')
        self.assertContains(response, 'pending-panel')

    def test_my_vibes_lists_file_name_and_bytes(self):
        project = make_project(self.owner, self.cat, title='Queue card', status='pending',
                               html_code='<p>hi</p>')
        response = self.client.get('/my-vibes/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Queue card')
        self.assertContains(response, 'bytes')
        self.assertContains(response, 'not a hang')

    def test_scan_status_owner_gets_file_receipt_and_phase(self):
        project = make_project(self.owner, self.cat, title='Scan me', status='pending')
        project.zip_file.save('payload.zip', make_zip_file({'a.py': 'x'}), save=True)
        ScanJob.objects.create(project=project, status='scanning')
        response = self.client.get(f'/app/{project.slug}/scan-status/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'scanning')
        self.assertFalse(body['is_published'])
        self.assertTrue(body['reason'])
        self.assertEqual(body['phase'], 'scanning')
        self.assertTrue(body['poll'])
        self.assertIn('payload.zip', body['file_name'])
        self.assertGreater(body['file_bytes'], 0)
        self.assertIn('bytes', body['file_bytes_label'])
        self.assertTrue(body['headline'])
        self.assertTrue(body['steps'])

    def test_human_hold_slows_the_poll(self):
        project = make_project(
            self.owner, self.cat, title='Human hold', status='pending',
            html_code='<p>ok</p>',
        )
        response = self.client.get(f'/app/{project.slug}/scan-status/')
        body = response.json()
        self.assertEqual(body['phase'], 'human_review')
        self.assertTrue(body['poll'])
        self.assertGreaterEqual(body['poll_ms'], 8000)
        self.assertIn('human', body['headline'].lower() + body['why_waiting'].lower())

    def test_stranger_published_scan_has_no_file_name(self):
        project = make_project(self.owner, self.cat, title='Live already', status='published')
        self.client.logout()
        response = self.client.get(f'/app/{project.slug}/scan-status/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['is_published'])
        self.assertEqual(body['reason'], '')
        self.assertNotIn('file_name', body)

    def test_moderator_approve_writes_published_notification(self):
        mod = make_user('waitmod', role='moderator')
        held = make_project(self.owner, self.cat, title='Please approve', status='pending',
                            html_code='<p>ok</p>')
        self.client.force_login(mod)
        response = self.client.post(f'/moderation/{held.slug}/', {'action': 'approve'}, follow=True)
        self.assertEqual(response.status_code, 200)
        held.refresh_from_db()
        self.assertEqual(held.status, 'published')
        note = Notification.objects.filter(user=self.owner, kind='published').first()
        self.assertIsNotNone(note)
        self.assertIn('live', note.title.lower())

    def test_receipt_for_snippet_counts_source_bytes(self):
        project = make_project(
            self.owner, self.cat, title='Byte snippet', status='pending',
            html_code='<p>abc</p>', css_code='p{}', js_code='',
        )
        receipt = upload_receipt(project)
        self.assertEqual(receipt['kind'], 'snippet')
        self.assertTrue(receipt['file_name'].endswith('.html'))
        self.assertEqual(
            receipt['file_bytes'],
            len(b'<p>abc</p>') + len(b'p{}'),
        )
        self.assertIn('bytes', receipt['file_bytes_label'])

    def test_notify_queued_survives_missing_owner(self):
        project = make_project(self.owner, self.cat, title='No owner ping', status='pending')
        project.owner = None
        notify_queued(project)  # must not raise
        self.assertFalse(Notification.objects.filter(kind='queued', title__contains='No owner ping').exists())

    def test_inbox_renders_queued_kind_label(self):
        project = make_project(self.owner, self.cat, title='Inbox label', status='pending',
                               html_code='<p>x</p>')
        notify_queued(project)
        response = self.client.get('/inbox/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Waiting for approval')
        self.assertContains(response, 'bytes')
