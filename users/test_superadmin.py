"""Superadmin provisioning + login regression tests.

5 Whys: why pin these in CI?
1. Why a command test at all? The original bug was silent: `createsuperuser`
   produced an account that logged in, reached /blaq-admin-secure/, then 403'd
   on every app admin page because profile.role stayed 'user'. A test that
   asserts BOTH the Django flags and the app role fails loudly if the command
   ever drifts back to half-provisioned.
2. Why test idempotency? Deploy scripts re-run provisioning; a second run
   must converge, not raise on the duplicate username or reset role.
3. Why test repair of an existing half-configured user? That is exactly the
   state a broken deploy leaves behind — the command's main job is fixing it.
4. Why an email-login test? settings promise ACCOUNT_LOGIN_METHODS email,
   but the site login is a stock AuthenticationForm that ignores it. The
   regression ("sign in with your email" fails) only stays fixed if a test
   watches it.
5. Why assert the ambiguous-email case does NOT redirect? Two accounts
   sharing an address must not make login a coin flip — the clean() only
   resolves when exactly one match exists, and the test locks that in.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase

from .models import Profile

User = get_user_model()
PW = 'Admin@BlaqVibe2026'


class CreateSuperadminTest(TestCase):
    def test_creates_with_all_flags_and_role(self):
        call_command('create_superadmin', username='admin',
                     email='admin@blaqvibes.co.za', password=PW)
        u = User.objects.get(username='admin')
        self.assertTrue(u.is_staff)
        self.assertTrue(u.is_superuser)
        self.assertEqual(u.email, 'admin@blaqvibes.co.za')
        self.assertTrue(u.check_password(PW))
        self.assertEqual(u.profile.role, 'superadmin')
        self.assertTrue(u.profile.email_verified)

    def test_idempotent_rerun(self):
        for _ in range(2):
            call_command('create_superadmin', username='admin',
                         email='admin@blaqvibes.co.za', password=PW)
        self.assertEqual(User.objects.filter(username='admin').count(), 1)

    def test_repairs_half_configured_superadmin(self):
        # The exact state `createsuperuser` leaves behind: Django flags set,
        # app role still 'user' — the "it never works" account.
        u = User.objects.create_user('admin', 'admin@blaqvibes.co.za', PW)
        u.is_staff = u.is_superuser = True
        u.save()
        self.assertEqual(u.profile.role, 'user')  # precondition
        call_command('create_superadmin', username='admin',
                     email='admin@blaqvibes.co.za', password=PW)
        u.refresh_from_db()
        self.assertEqual(u.profile.role, 'superadmin')
        self.assertTrue(u.profile.email_verified)

    def test_superadmin_reaches_app_admin_pages(self):
        call_command('create_superadmin', username='admin',
                     email='admin@blaqvibes.co.za', password=PW)
        c = Client()
        ok = c.login(username='admin', password=PW)
        self.assertTrue(ok)
        for path in ('/admin/dashboard/', '/admin/roles/', '/admin/audit/',
                     '/blaq-admin-secure/'):
            self.assertEqual(c.get(path).status_code, 200, path)

    def test_plain_superuser_is_forbidden_on_app_admin_pages(self):
        u = User.objects.create_user('ghost', 'g@example.com', PW)
        u.is_staff = u.is_superuser = True
        u.save()
        c = Client()
        c.login(username='ghost', password=PW)
        self.assertEqual(c.get('/admin/roles/').status_code, 403)


class EmailLoginTest(TestCase):
    def setUp(self):
        u = User.objects.create_user('admin', 'admin@blaqvibes.co.za', PW)
        # The post_save receiver on User already created a Profile, so a bare
        # create() here raises IntegrityError on users_profile.user_id.
        Profile.objects.update_or_create(
            user=u, defaults={'role': 'superadmin', 'email_verified': True})
        self.client = Client()

    def test_login_with_email(self):
        r = self.client.post('/accounts/login/', follow=True,
                             data={'username': 'admin@blaqvibes.co.za',
                                   'password': PW})
        self.assertTrue(r.wsgi_request.user.is_authenticated)
        self.assertEqual(r.wsgi_request.user.username, 'admin')

    def test_login_with_username_still_works(self):
        r = self.client.post('/accounts/login/', follow=True,
                             data={'username': 'admin', 'password': PW})
        self.assertTrue(r.wsgi_request.user.is_authenticated)

    def test_wrong_password_rejected(self):
        self.client.post('/accounts/login/', follow=True,
                         data={'username': 'admin@blaqvibes.co.za',
                               'password': 'nope'})
        self.assertFalse(bool(self.client.session.get('_auth_user_id')))

    def test_ambiguous_email_does_not_pick_a_user(self):
        User.objects.create_user('other', 'admin@blaqvibes.co.za', PW)
        r = self.client.post('/accounts/login/', follow=True,
                             data={'username': 'admin@blaqvibes.co.za',
                                   'password': PW})
        self.assertFalse(r.wsgi_request.user.is_authenticated)
