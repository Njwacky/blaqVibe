from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse

from .models import SecurityEvent

class PasswordSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('secure-user', 'secure@example.com', 'OldPassword123!')
        self.current = self.client
        self.other = Client()
        self.current.login(username='secure-user', password='OldPassword123!')
        self.other.login(username='secure-user', password='OldPassword123!')
        self.other_key = self.other.session.session_key

    def test_password_change_revokes_other_server_sessions(self):
        response = self.current.post(reverse('password_change'), {
            'old_password': 'OldPassword123!',
            'new_password1': 'NewPassword123!',
            'new_password2': 'NewPassword123!',
        })
        self.assertRedirects(response, reverse('password_change_done'))
        self.assertFalse(Session.objects.filter(session_key=self.other_key).exists())
        self.assertTrue(self.current.get(reverse('settings')).wsgi_request.user.is_authenticated)
        self.assertTrue(SecurityEvent.objects.filter(user=self.user, event='password_changed').exists())

    def test_login_audits_first_recognised_and_new_device(self):
        self.client.logout()
        self.client.post(reverse('login'), {'username': 'secure-user', 'password': 'OldPassword123!'}, HTTP_USER_AGENT='device-one')
        self.client.logout()
        self.client.post(reverse('login'), {'username': 'secure-user', 'password': 'OldPassword123!'}, HTTP_USER_AGENT='device-two')
        self.assertTrue(SecurityEvent.objects.filter(user=self.user, event='login_first_device').exists())
        self.assertTrue(SecurityEvent.objects.filter(user=self.user, event='login_new_device').exists())

    def test_password_change_revokes_git_token(self):
        self.user.profile.rotate_git_token()
        self.current.post(reverse('password_change'), {
            'old_password': 'OldPassword123!',
            'new_password1': 'NewPassword123!',
            'new_password2': 'NewPassword123!',
        })
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.git_token_hash, '')

    def test_owner_can_revoke_other_devices_without_changing_password(self):
        response = self.current.post(reverse('logout_other_devices'))
        self.assertRedirects(response, reverse('account_security'))
        self.assertFalse(Session.objects.filter(session_key=self.other_key).exists())

    def test_settings_shows_accounts_and_security_before_the_toggles(self):
        response = self.current.get(reverse('settings'))
        body = response.content.decode()
        self.assertContains(response, 'Accounts &amp; Security')
        self.assertLess(body.index('settings-jump'), body.index('Notifications'))
        self.assertIn('/settings/account/', body)
        self.assertNotContains(response, 'Delete my account')
        self.assertNotContains(response, 'Git access')
        self.assertNotContains(response, 'Change password')
        self.assertContains(response, 'Show the floating feedback button')
        self.assertContains(response, 'data-key="notify_on_star"')

    def test_account_security_page_holds_password_logout_and_delete(self):
        response = self.current.get(reverse('account_security'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change password')
        self.assertContains(response, 'Log out')
        self.assertContains(response, 'Sign out other devices')
        self.assertContains(response, 'Git access')
        self.assertContains(response, 'Connected accounts')
        self.assertContains(response, 'Delete my account')
        body = response.content.decode()
        # The nav keeps its hidden logout form. This page posts to the same
        # URL with its own form and must not duplicate that id.
        self.assertEqual(body.count('id="logout-form"'), 1)
        self.assertIn('action="/accounts/logout/"', body)
        self.assertNotContains(response, 'settings.js')
