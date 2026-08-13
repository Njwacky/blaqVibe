from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from users.forms import SignUpForm
from users.models import Profile


@override_settings(RATELIMIT_ENABLE=False)
class AuthAndProTests(TestCase):
    def test_signup_requires_email(self):
        form = SignUpForm(data={
            'username': 'newbie',
            'email': '',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_signup_creates_user_with_email(self):
        response = self.client.post('/accounts/signup/', {
            'username': 'newbie',
            'email': 'newbie@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='newbie')
        self.assertEqual(user.email, 'newbie@test.com')
        self.assertTrue(hasattr(user, 'profile'))

    def test_pro_trial_expires(self):
        user = User.objects.create_user('prouser', password='pass12345', email='p@test.com')
        profile = user.profile
        profile.is_pro = True
        profile.pro_since = timezone.now() - timedelta(days=8)
        profile.pro_until = timezone.now() - timedelta(days=1)
        profile.save()
        self.assertFalse(profile.is_pro_active)

    def test_pro_trial_is_seven_days_and_one_shot(self):
        user = User.objects.create_user('trial', password='pass12345', email='t@test.com')
        self.client.login(username='trial', password='pass12345')
        response = self.client.post('/pro/activate/')
        self.assertEqual(response.status_code, 302)
        profile = Profile.objects.get(user=user)
        self.assertTrue(profile.is_pro_active)
        self.assertIsNotNone(profile.pro_until)
        self.assertGreater(profile.pro_until, timezone.now() + timedelta(days=6))

        profile.pro_until = timezone.now() - timedelta(minutes=1)
        profile.save(update_fields=['pro_until'])
        response = self.client.post('/pro/activate/')
        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertFalse(profile.is_pro_active)

    def test_verify_email_marks_profile(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        user = User.objects.create_user('mailer', password='pass12345', email='m@test.com')
        self.assertFalse(user.profile.email_verified)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        response = self.client.get(f'/accounts/verify/{uid}/{token}/')
        self.assertEqual(response.status_code, 302)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.email_verified)

    def test_login_hides_social_when_unconfigured(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Continue with Google')

    @override_settings(GOOGLE_CLIENT_ID='test-google-id.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET='secret')
    def test_login_shows_google_when_configured(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continue with Google')
        self.assertContains(response, '/accounts/social/google/login/')

    def test_delete_account_requires_username(self):
        user = User.objects.create_user('goner', password='pass12345', email='g@test.com')
        self.client.login(username='goner', password='pass12345')
        response = self.client.post('/settings/delete-account/', {'confirm': 'wrong'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='goner').exists())
        response = self.client.post('/settings/delete-account/', {'confirm': 'goner'})
        self.assertFalse(User.objects.filter(username='goner').exists())
