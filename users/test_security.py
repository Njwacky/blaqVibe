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
        self.assertRedirects(response, reverse('settings'))
        self.assertFalse(Session.objects.filter(session_key=self.other_key).exists())
