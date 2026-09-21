"""Feedback conversations: the backend of the glowing floating button.

Covers the contract the feature promises:
- anonymous visitors are turned to the login screen
- a user can start a conversation and keep replying in their own
- a user can never read or write somebody else's (404, not a leak)
- every user message lands in EVERY superadmin's inbox (a Notification
  row linking to the admin conversation) and an email goes out
- only role 'superadmin' may open the queue or reply (admin/moderator 403)
- a superadmin reply lands back in the user's inbox and marks the thread
  read for staff; close/reopen works
- a closed conversation stops the user from posting
"""
from io import BytesIO

from PIL import Image

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import FeedbackMessage, FeedbackThread

User = get_user_model()
PW = 'BlaqVibe@Test2026'


def _make(username, role='user', email=None):
    u = User.objects.create_user(username, email or f'{username}@example.com', PW)
    u.profile.role = role
    u.profile.save()
    return u


def _thread(owner, body='please help', from_user=True):
    t = FeedbackThread.objects.create(user=owner)
    FeedbackMessage.objects.create(
        thread=t, sender=owner, from_staff=not from_user, body=body,
    )
    t.last_user_message_at = timezone.now()
    t.save(update_fields=['last_user_message_at'])
    return t


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-feedback')
class FeedbackUserSideTests(TestCase):
    def setUp(self):
        self.user = _make('builder')
        self.other = _make('other')
        self.client.force_login(self.user)

    def _png_upload(self, name='error.png'):
        buf = BytesIO()
        Image.new('RGB', (12, 8), (124, 58, 237)).save(buf, format='PNG')
        return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get(reverse('feedback_inbox'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    def test_feedback_accepts_a_screenshot_and_serves_it_only_to_owner(self):
        resp = self.client.post(
            reverse('feedback_inbox'),
            {'body': 'The error appears after tapping Publish.', 'attachment': self._png_upload()},
        )
        self.assertEqual(resp.status_code, 302)
        message = FeedbackMessage.objects.get(thread__user=self.user)
        self.assertTrue(message.attachment.name.startswith('feedback/'))

        image = self.client.get(reverse('feedback_attachment', args=(message.thread_id, message.pk)))
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image['Content-Type'], 'image/png')
        self.assertEqual(image['Content-Disposition'], 'inline; filename="feedback-screenshot.png"')

        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse('feedback_attachment', args=(message.thread_id, message.pk))).status_code,
            404,
        )

    def test_screenshot_alone_starts_a_readable_message(self):
        self.client.post(reverse('feedback_inbox'), {'attachment': self._png_upload('only.png')})
        message = FeedbackMessage.objects.get(thread__user=self.user)
        self.assertEqual(message.body, 'Screenshot attached.')

    def test_empty_body_creates_nothing(self):
        resp = self.client.post(reverse('feedback_inbox'), {'body': '   '})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(FeedbackThread.objects.count(), 0)

    def test_create_thread_notifies_every_superadmin(self):
        sa1 = _make('boss', role='superadmin', email='boss@blaqvibes.co.za')
        sa2 = _make('second', role='superadmin', email='second@blaqvibes.co.za')
        # A plain admin is NOT in the superadmin inbox — the channel is
        # deliberately personal (users/feedback.py docstring).
        _make('admin1', role='admin', email='admin1@blaqvibes.co.za')

        resp = self.client.post(
            reverse('feedback_inbox'), {'body': 'The publish page felt slow on mobile.'},
        )
        self.assertEqual(resp.status_code, 302)

        thread = FeedbackThread.objects.get(user=self.user)
        self.assertEqual(thread.messages.count(), 1)
        self.assertIsNotNone(thread.last_user_message_at)
        self.assertEqual(thread.status, 'open')

        for sa in (sa1, sa2):
            note = sa.notifications.filter(kind='feedback').get()
            self.assertIn(str(thread.pk), note.url)
            self.assertEqual(note.url, f'/admin/feedback/{thread.pk}/')
        # One email per superadmin with a real address — not to the admin.
        self.assertEqual(len(mail.outbox), 2)
        recipients = {to for msg in mail.outbox for to in msg.to}
        self.assertEqual(recipients, {'boss@blaqvibes.co.za', 'second@blaqvibes.co.za'})

    def test_cannot_open_others_conversation(self):
        t = _thread(self.other)
        resp = self.client.get(reverse('feedback_conversation', args=[t.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_reply_in_own_thread_pings_superadmin_again(self):
        sa = _make('boss', role='superadmin', email='boss@blaqvibes.co.za')
        t = _thread(self.user)
        resp = self.client.post(reverse('feedback_conversation', args=[t.pk]), {'body': 'following up'})
        self.assertEqual(resp.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.messages.count(), 2)
        # The follow-up view call fans out again (the first message was
        # written directly in this test, bypassing the view's notify step).
        self.assertEqual(sa.notifications.filter(kind='feedback').count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_closed_thread_blocks_user_posts(self):
        t = FeedbackThread.objects.create(user=self.user, status='closed')
        resp = self.client.post(reverse('feedback_conversation', args=[t.pk]), {'body': 'hi again'})
        self.assertEqual(resp.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.messages.count(), 0)
        self.assertEqual(t.status, 'closed')


class FeedbackAdminSideTests(TestCase):
    def setUp(self):
        self.builder = _make('builder')
        self.plain = _make('plain')
        self.moderator = _make('mod1', role='moderator')
        self.admin = _make('admin1', role='admin')
        self.sa = _make('boss', role='superadmin', email='boss@blaqvibes.co.za')
        self.thread = _thread(self.builder, 'the feed hides my vibe')

    def test_only_superadmin_reaches_queue(self):
        for u in (self.plain, self.moderator, self.admin):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse('admin_feedback_queue')).status_code, 403)
            self.assertEqual(
                self.client.get(reverse('admin_feedback_conversation', args=[self.thread.pk])).status_code,
                403,
            )

    def test_superadmin_queue_shows_thread_unread_first(self):
        # A second, answered-and-read thread must sort below the unread one.
        t2 = _thread(self.builder, 'second topic')
        t2.status = 'answered'
        t2.admin_last_read_at = timezone.now()
        t2.save(update_fields=['status', 'admin_last_read_at'])

        self.client.force_login(self.sa)
        resp = self.client.get(reverse('admin_feedback_queue'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '@builder')
        # The unread thread sorts before the read one.
        pos_unread = resp.content.decode().index(f'conversation #{self.thread.pk}')
        pos_read = resp.content.decode().index(f'conversation #{t2.pk}')
        self.assertLess(pos_unread, pos_read)

    def test_opening_conversation_marks_read(self):
        self.client.force_login(self.sa)
        self.assertTrue(self.thread.unread_for_staff())
        resp = self.client.get(reverse('admin_feedback_conversation', args=[self.thread.pk]))
        self.assertEqual(resp.status_code, 200)
        self.thread.refresh_from_db()
        self.assertIsNotNone(self.thread.admin_last_read_at)
        self.assertFalse(self.thread.unread_for_staff())

    def test_empty_reply_creates_nothing(self):
        self.client.force_login(self.sa)
        self.client.post(
            reverse('admin_feedback_conversation', args=[self.thread.pk]),
            {'action': 'reply', 'body': '   '},
        )
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.messages.count(), 1)
        self.assertEqual(self.thread.status, 'open')

    def test_reply_notifies_user_and_marks_answered(self):
        self.client.force_login(self.sa)
        resp = self.client.post(
            reverse('admin_feedback_conversation', args=[self.thread.pk]),
            {'action': 'reply', 'body': 'Thanks — fixing this week.'},
        )
        self.assertEqual(resp.status_code, 302)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.status, 'answered')
        last = self.thread.messages.order_by('created_at', 'id').last()
        self.assertTrue(last.from_staff)
        self.assertEqual(last.sender, self.sa)

        # The builder sees the reply in their regular inbox, linked to their
        # own view of the thread.
        note = self.builder.notifications.filter(kind='feedback').latest('id')
        self.assertEqual(note.url, f'/feedback/{self.thread.pk}/')
        # No email for a reply — this is a chat, not an alert.
        self.assertEqual(len(mail.outbox), 0)

    def test_close_and_reopen(self):
        self.client.force_login(self.sa)
        self.client.post(
            reverse('admin_feedback_conversation', args=[self.thread.pk]),
            {'action': 'close'},
        )
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.status, 'closed')
        # A closed thread no longer shows in the user's reply box.
        self.client.force_login(self.builder)
        self.client.post(reverse('feedback_conversation', args=[self.thread.pk]), {'body': 'back'})
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.messages.count(), 1)
        self.client.force_login(self.sa)
        self.client.post(
            reverse('admin_feedback_conversation', args=[self.thread.pk]),
            {'action': 'reopen'},
        )
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.status, 'open')


class FeedbackBadgeTests(TestCase):
    """The nav badge (gallery.context_processors) counts unread threads for
    superadmins only — the glowing button promised a human would read it."""

    @override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-feedback')
    def test_feedback_fab_is_on_by_default_and_can_be_hidden(self):
        user = _make('builder')
        self.client.force_login(user)
        self.assertContains(self.client.get('/'), 'id="bv-fab"')

        off = self.client.post(
            reverse('toggle_setting'),
            {'key': 'show_feedback_fab', 'value': 'false'},
        )
        self.assertEqual(off.status_code, 200)
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.show_feedback_fab)
        self.assertNotContains(self.client.get('/'), 'id="bv-fab"')

        on = self.client.post(
            reverse('toggle_setting'),
            {'key': 'show_feedback_fab', 'value': 'true'},
        )
        self.assertEqual(on.status_code, 200)
        self.assertContains(self.client.get('/'), 'id="bv-fab"')

    @override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-feedback')
    def test_feedback_fab_tip_has_ok_dismiss_button_and_can_be_dismissed(self):
        user = _make('tip_user')
        self.client.force_login(user)
        resp = self.client.get('/')
        self.assertContains(resp, 'id="bv-fab"')
        self.assertContains(resp, 'id="bv-fab-tip"')
        self.assertContains(resp, 'id="bv-fab-tip-dismiss"')
        self.assertContains(resp, '>OK</button>')
        self.assertContains(resp, 'aria-describedby="bv-fab-tip"')

        # Dismiss via toggle_setting endpoint
        dismiss = self.client.post(
            reverse('toggle_setting'),
            {'key': 'feedback_fab_tip_dismissed', 'value': 'true'},
        )
        self.assertEqual(dismiss.status_code, 200)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.feedback_fab_tip_dismissed)

        # After dismissal, FAB icon remains, but tip message is gone
        resp_after = self.client.get('/')
        self.assertContains(resp_after, 'id="bv-fab"')
        self.assertNotContains(resp_after, 'id="bv-fab-tip"')
        self.assertNotContains(resp_after, 'aria-describedby="bv-fab-tip"')

    @override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-feedback')
    def test_feedback_fab_tip_dismissed_via_cookie(self):
        user = _make('cookie_user')
        self.client.force_login(user)
        self.client.cookies['blaq_fab_tip_dismissed'] = '1'
        resp = self.client.get('/')
        self.assertContains(resp, 'id="bv-fab"')
        self.assertNotContains(resp, 'id="bv-fab-tip"')
        self.assertNotContains(resp, 'aria-describedby="bv-fab-tip"')

    def test_feedback_fab_tip_not_shown_for_anonymous_user(self):
        resp = self.client.get('/')
        self.assertContains(resp, 'id="bv-fab"')
        self.assertNotContains(resp, 'id="bv-fab-tip"')

    def test_superadmin_sees_unread_count(self):
        builder = _make('builder')
        sa = _make('boss', role='superadmin')
        _thread(builder)  # unread: a user message, never opened by staff
        self.client.force_login(sa)
        resp = self.client.get('/')
        self.assertContains(resp, 'Feedback inbox')
        self.assertContains(resp, '<span class="nav-badge">1</span>')

    def test_non_superadmin_has_no_badge_link(self):
        builder = _make('builder')
        t = _thread(builder)
        self.client.force_login(builder)
        resp = self.client.get('/')
        self.assertNotContains(resp, 'Feedback inbox')

    def test_read_thread_drops_count(self):
        builder = _make('builder')
        sa = _make('boss', role='superadmin')
        t = _thread(builder)
        t.admin_last_read_at = timezone.now()
        t.save(update_fields=['admin_last_read_at'])
        self.client.force_login(sa)
        resp = self.client.get('/')
        self.assertContains(resp, 'Feedback inbox')
        self.assertNotContains(resp, '<span class="nav-badge">1</span>')
