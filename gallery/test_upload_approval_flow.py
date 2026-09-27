"""The upload → review → decision loop, end to end.

Regression suite for the "nobody is told anything" malfunction:

* A vibe is uploaded and STAFF see it — including a fresh-install operator
  whose only account came from `createsuperuser` (Django flags set, but
  profile.role still the default 'user'). Before this suite's fixes the
  fan-out matched neither staff axis and landed with zero recipients.
* The UPLOADER gets a receipt ("your application is in") the moment the
  ZIP is queued, a state row when the verdict is "waiting on a human", and
  a final row + email when a moderator approves or rejects. Approving from
  the moderation queue used to be completely silent to the builder.
* One decision = one inbox row + one email. The queue-entry and scan-verdict
  fan-outs used to double-post identical "Pending approval" rows.
* An auto-publish clears the stale "Pending approval" rows it just made
  irrelevant, so the staff badge never asks for a decision that is gone.
"""
import io
import zipfile

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from gallery.models import AppProject, Notification
from gallery.tests import make_category, make_project, make_user


def make_zip(files=None):
    files = files or {'index.html': '<h1>hi</h1>'}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return SimpleUploadedFile('app.zip', buf.getvalue(), content_type='application/zip')


def upload_zip(client, title='My First Vibe'):
    return client.post('/publish/', {
        'title': title,
        'short_description': 'A first vibe used to exercise the review loop.',
        'build_method': 'human',
        'zip_file': make_zip(),
    })


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class FreshInstallStaffTests(TestCase):
    """The fresh-install case: one operator account made by createsuperuser."""

    def setUp(self):
        self.cat = make_category()
        # profile.role stays the default 'user' — exactly what stranded
        # fresh installs before the fan-out learned the Django axis.
        self.operator = User.objects.create_superuser('operator', 'op@test.com', 'pass12345')

    def test_superuser_without_app_role_gets_the_upload_notification(self):
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        response = upload_zip(c)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Notification.objects.filter(user=self.operator, kind='approval').exists(),
            'A createsuperuser operator must see the upload in their inbox.',
        )

    def test_superuser_without_app_role_can_open_queue_and_approve(self):
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)
        project = AppProject.objects.get(owner=newbie)

        op = Client()
        op.force_login(self.operator)
        self.assertEqual(op.get('/moderation/queue/').status_code, 200,
                         'The notification links to /moderation/queue/ — it must open.')
        response = op.post(f'/moderation/{project.slug}/', {'action': 'approve'})
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db()
        self.assertEqual(project.status, 'published')

    def test_plain_member_gets_nothing_and_cannot_open_queue(self):
        bystander = make_user('bystander')
        newbie = make_user('newbie2')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)
        self.assertFalse(Notification.objects.filter(user=bystander).exists())
        b = Client()
        b.force_login(bystander)
        self.assertEqual(b.get('/moderation/queue/').status_code, 403)

    def test_new_user_signup_is_announced_to_staff(self):
        response = self.client.post('/accounts/signup/', {
            'username': 'freshbuilder',
            'email': 'fresh@test.com',
            'password1': 'v3ry-Secret-99x',
            'password2': 'v3ry-Secret-99x',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Notification.objects.filter(user=self.operator, title__startswith='New user:').exists(),
            'Staff must see new signups land.',
        )


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class UploaderSideTests(TestCase):
    """What the person who pressed upload sees in their own inbox."""

    def setUp(self):
        self.cat = make_category()
        self.mod = make_user('mod1', role='moderator')

    def test_owner_gets_a_receipt_the_moment_the_zip_is_queued(self):
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)
        self.assertTrue(
            Notification.objects.filter(user=newbie, kind='upload',
                                        title__startswith='Application received:').exists(),
            'The uploader must see that the application was sent, even after closing the tab.',
        )

    def test_owner_is_told_when_review_is_needed(self):
        """A clean scan for a NEW account holds for a human — the owner must
        see that state in-app, not only in an email that may never arrive."""
        from gallery.tasks import finalize_publish
        newbie = make_user('newbie')
        project = make_project(newbie, self.cat, status='pending', title='Awaiting Review')
        project.zip_file = None
        project.scan_report = {'clamav': 'clean'}
        project.save()
        finalize_publish(project_id=project.pk)
        project.refresh_from_db()
        self.assertEqual(project.status, 'pending')
        self.assertTrue(
            Notification.objects.filter(user=newbie, kind='review_needed').exists(),
            'pending_review_needed must leave an inbox row for the owner.',
        )

    def test_approve_from_queue_reaches_the_owner_and_sends_email(self):
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)
        project = AppProject.objects.get(owner=newbie)

        mail.outbox = []
        mod = Client()
        mod.force_login(self.mod)
        response = mod.post(f'/moderation/{project.slug}/', {'action': 'approve'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Notification.objects.filter(user=newbie, kind='published').exists(),
            'A moderator approval must tell the owner their vibe is live.',
        )
        self.assertTrue(any(project.title in m.subject for m in mail.outbox),
                        'The owner must also get the status email on a human decision.')

    def test_reject_from_queue_reaches_the_owner(self):
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)
        project = AppProject.objects.get(owner=newbie)

        mod = Client()
        mod.force_login(self.mod)
        mod.post(f'/moderation/{project.slug}/', {'action': 'reject'})
        project.refresh_from_db()
        self.assertEqual(project.status, 'quarantined')
        self.assertTrue(
            Notification.objects.filter(user=newbie, kind='quarantined',
                                        title__contains='not approved').exists(),
            'A rejection must be told to the owner, not discovered by accident.',
        )


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class StaffNoiseTests(TestCase):
    """One decision = one row + one email, and stale rows get cleared."""

    def setUp(self):
        self.cat = make_category()
        self.mod = make_user('mod1', role='moderator')

    def test_one_upload_is_one_row_and_one_email(self):
        """The queue-entry fan-out and the scan-verdict fan-out describe the
        SAME decision; the second must stay quiet while the first is unread."""
        from gallery.tasks import finalize_publish
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)  # fan-out #1 (queue entry) + pipeline
        project = AppProject.objects.get(owner=newbie)
        # Fan-out #2 with the same decision, as the scan verdict would.
        from gallery.admin_notifications import notify_admins_pending_project
        notify_admins_pending_project(project, reason='second fan-out for the same decision')

        rows = Notification.objects.filter(user=self.mod, kind='approval', is_read=False)
        self.assertEqual(rows.count(), 1, 'duplicate unread rows for one decision')
        approval_mails = [m for m in mail.outbox if 'Approval Needed' in m.subject]
        self.assertEqual(len(approval_mails), 1, 'duplicate emails for one decision')

    def test_read_row_allows_reescalation(self):
        from gallery.admin_notifications import notify_admins_pending_project
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)
        project = AppProject.objects.get(owner=newbie)
        Notification.objects.filter(user=self.mod, kind='approval').update(is_read=True)
        notify_admins_pending_project(project, reason='still pending, row was read')
        self.assertEqual(
            Notification.objects.filter(user=self.mod, kind='approval', is_read=False).count(),
            1, 'once the row is read a fresh fan-out escalates again',
        )

    def test_auto_publish_clears_the_stale_staff_row(self):
        from gallery.tasks import finalize_publish
        veteran = make_user('veteran')
        for i in range(3):
            make_project(veteran, self.cat, title=f'Old vibe {i}')
        project = make_project(veteran, self.cat, status='pending', title='Auto One')
        project.scan_report = {'clamav': 'clean'}
        project.save()
        from gallery.admin_notifications import notify_admins_pending_project
        notify_admins_pending_project(project, reason='queue entry')
        self.assertTrue(
            Notification.objects.filter(user=self.mod, kind='approval', is_read=False).exists())

        finalize_publish(project_id=project.pk)
        project.refresh_from_db()
        self.assertEqual(project.status, 'published')
        self.assertFalse(
            Notification.objects.filter(user=self.mod, kind='approval', is_read=False).exists(),
            'auto-publish must clear the "Pending approval" row it just made stale',
        )

    def test_approve_clears_rows_for_every_staff_member(self):
        other_mod = make_user('mod2', role='moderator')
        newbie = make_user('newbie')
        c = Client()
        c.force_login(newbie)
        upload_zip(c)
        project = AppProject.objects.get(owner=newbie)
        self.assertTrue(Notification.objects.filter(user=other_mod, kind='approval',
                                                    is_read=False).exists())
        mod = Client()
        mod.force_login(self.mod)
        mod.post(f'/moderation/{project.slug}/', {'action': 'approve'})
        self.assertFalse(
            Notification.objects.filter(user=other_mod, kind='approval', is_read=False).exists(),
            'once one moderator decides, the same decision must stop badging the others',
        )


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class RecipientFanOutTests(TestCase):
    def setUp(self):
        self.cat = make_category()

    def test_fan_out_covers_both_staff_axes(self):
        from gallery.admin_notifications import get_admin_users_for_notification
        role_mod = make_user('role_mod', role='moderator')
        dj_staff = User.objects.create_user('dj_staff', 'stf@test.com', 'pass12345', is_staff=True)
        dj_super = User.objects.create_superuser('dj_super', 'sup@test.com', 'pass12345')
        pleb = make_user('pleb')
        inactive_mod = make_user('gone', role='moderator')
        inactive_mod.is_active = False
        inactive_mod.save()

        found = set(get_admin_users_for_notification().values_list('username', flat=True))
        self.assertIn('role_mod', found)
        self.assertIn('dj_staff', found)
        self.assertIn('dj_super', found)
        self.assertNotIn('pleb', found)
        self.assertNotIn('gone', found)

    def test_admin_without_email_still_gets_the_in_app_row(self):
        from gallery.admin_notifications import notify_admins_for_approval
        no_mail = User.objects.create_user('nomail', '', 'pass12345')
        no_mail.profile.role = 'moderator'
        no_mail.profile.save()
        notify_admins_for_approval('approval', 'Pending approval: X', 'body', '/moderation/queue/')
        self.assertTrue(Notification.objects.filter(user=no_mail, kind='approval').exists(),
                        'no email on file must not silence the in-app inbox')
