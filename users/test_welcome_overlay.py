"""First-time welcome overlay — users/welcome.py + welcome_views + base.html.

The overlay is the on-ramp: shown once per account (full-screen, after first
sign-in) and never again. These tests pin three promises:

  1. New accounts see it; answered accounts never do (server truth).
  2. `mark_seen` is idempotent — ten tabs POSTing can't re-open it.
  3. The POST endpoint is login-bound, POST-only, and flips the flag.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from gallery.models import AppProject, Category
from users import welcome


def make_user(username):
    return User.objects.create_user(username, password='pass12345', email=f'{username}@test.com')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class WelcomeOverlayTests(TestCase):
    def setUp(self):
        self.user = make_user('greenhorn')

    def test_new_account_still_has_welcome_pending(self):
        self.client.login(username='greenhorn', password='pass12345')
        response = self.client.get('/')
        self.assertContains(response, 'id="welcome-overlay"')
        self.assertContains(response, 'Show us what you built.')
        self.assertContains(response, 'Upload my first app')
        self.assertContains(response, 'Explore first')

    def test_upload_handoff_marks_welcome_seen_before_rendering_publish(self):
        self.client.login(username='greenhorn', password='pass12345')
        response = self.client.get('/publish/?welcome=1')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.overlay_seen)
        self.assertNotContains(response, 'id="welcome-overlay"')
        self.assertContains(response, 'Publish')

    def test_overlay_absent_for_anonymous_feed(self):
        response = self.client.get('/')
        self.assertNotContains(response, 'id="welcome-overlay"')
        self.assertNotContains(response, 'Make it visible')

    def test_seen_account_never_renders_overlay_again(self):
        welcome.mark_seen(self.user)
        self.client.login(username='greenhorn', password='pass12345')
        response = self.client.get('/')
        self.assertNotContains(response, 'id="welcome-overlay"')

    def test_mark_seen_is_idempotent_and_never_unsets(self):
        self.assertFalse(self.user.profile.overlay_seen)
        welcome.mark_seen(self.user)
        welcome.mark_seen(self.user)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.overlay_seen)

    def test_welcome_seen_endpoint_requires_post(self):
        self.client.login(username='greenhorn', password='pass12345')
        response = self.client.get('/welcome/seen')
        self.assertEqual(response.status_code, 405)

    def test_welcome_seen_endpoint_requires_login(self):
        response = self.client.post('/welcome/seen')
        self.assertEqual(response.status_code, 302)  # login redirect

    def test_welcome_seen_flips_flag_via_json(self):
        self.client.login(username='greenhorn', password='pass12345')
        response = self.client.post(
            '/welcome/seen', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True})
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.overlay_seen)

    def test_should_show_overlay_guards_anonymous_and_none(self):
        self.assertFalse(welcome.should_show_overlay(None))
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(welcome.should_show_overlay(AnonymousUser()))
