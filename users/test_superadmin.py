"""Superadmin provisioning + login regression tests.
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

class DemoStaffAndPlaceholderPasswordTest(TestCase):
    """The 'admin / youpwassword never works' regressions.
    """

    def test_local_seed_demo_staff_can_open_admin(self):
        from django.core.management import call_command
        with self.settings(LOCAL_DEV=True, DEBUG=False, SEED_DEMO=False):
            call_command('seed_demo')
        nolo = User.objects.get(username='nolo.ai')
        self.assertEqual(nolo.profile.role, 'superadmin')
        self.assertTrue(nolo.is_superuser)
        self.assertTrue(nolo.profile.email_verified)
        self.assertEqual(User.objects.get(username='blaq').profile.role, 'admin')
        self.assertEqual(User.objects.get(username='thando').profile.role, 'moderator')
        r = self.client.post('/accounts/login/', follow=True,
                             data={'username': 'nolo.ai', 'password': 'blaq12345'})
        self.assertTrue(r.wsgi_request.user.is_authenticated)
        self.assertEqual(self.client.get('/admin/dashboard/').status_code, 200)
        self.assertEqual(self.client.get('/admin/roles/').status_code, 200)

    def test_production_seed_is_refused_entirely(self):
        """No known-password accounts on a public host — not even role='user'.

        The old contract was "seed in production, but skip the staff rows", which
        still minted `blaq`/`thando` with the README passwords AND marked them
        email_verified with a funded wallet — the exact flag that unlocks
        trading, tipping and payout eligibility. The seeder now refuses outright.
        """
        from django.core.management import call_command
        from gallery.models import AppProject
        with self.settings(LOCAL_DEV=False, DEBUG=False, SEED_DEMO=False):
            with self.assertRaises(RuntimeError):
                call_command('seed_demo')
        for username in ('blaq', 'thando', 'nolo.ai'):
            self.assertFalse(User.objects.filter(username=username).exists())
        self.assertEqual(AppProject.objects.count(), 0)

    def test_forced_seed_publishes_catalog_without_credentials(self):
        """SEED_DEMO_FORCE=1 = content on a demo box, credentials off."""
        import os
        from unittest.mock import patch
        from django.core.management import call_command
        from gallery.models import AppProject
        with self.settings(LOCAL_DEV=False, DEBUG=False, SEED_DEMO=False), \
                patch.dict(os.environ, {'SEED_DEMO_FORCE': '1'}):
            call_command('seed_demo')
        self.assertTrue(AppProject.objects.filter(status='published').exists())
        blaq = User.objects.get(username='blaq')
        self.assertFalse(blaq.has_usable_password())
        self.assertFalse(blaq.profile.email_verified)
        self.assertEqual(blaq.profile.stars_balance, 0)
        self.assertEqual(blaq.profile.role, 'user')
        self.assertFalse(User.objects.filter(username='nolo.ai').exists())
        # …and the documented password cannot sign in as them.
        r = self.client.post('/accounts/login/', follow=True,
                             data={'username': 'blaq', 'password': 'blaq12345'})
        self.assertFalse(r.wsgi_request.user.is_authenticated)

    def test_createsuperuser_admin_is_repaired_so_placeholder_password_works(self):
        # The operator's exact leftover: createsuperuser + admin / youpwassword.
        u = User.objects.create_user('admin', 'admin@blaqvibes.co.za', 'youpwassword')
        u.is_staff = u.is_superuser = True
        u.save()
        self.assertEqual(u.profile.role, 'user')
        from gallery.seed import seed_demo
        with self.settings(LOCAL_DEV=True, DEBUG=False, SEED_DEMO=False):
            seed_demo()
        u.refresh_from_db()
        self.assertEqual(u.profile.role, 'superadmin')
        self.assertTrue(u.check_password('youpwassword'))
        r = self.client.post('/accounts/login/', follow=True,
                             data={'username': 'admin', 'password': 'youpwassword'})
        self.assertTrue(r.wsgi_request.user.is_authenticated)
        self.assertEqual(self.client.get('/admin/dashboard/').status_code, 200)

    def test_env_password_provisions_admin_youpwassword(self):
        import os
        from unittest.mock import patch
        from users.provision import maybe_provision_from_env
        env = {
            'DJANGO_SUPERADMIN_PASSWORD': 'youpwassword',
            'DJANGO_SUPERADMIN_USERNAME': 'admin',
            'DJANGO_SUPERADMIN_EMAIL': 'admin@blaqvibes.co.za',
        }
        with patch.dict(os.environ, env, clear=False):
            maybe_provision_from_env(ignore_testing=True)
        u = User.objects.get(username='admin')
        self.assertEqual(u.profile.role, 'superadmin')
        self.assertTrue(u.check_password('youpwassword'))
        from allauth.account.models import EmailAddress
        addr = EmailAddress.objects.get(email='admin@blaqvibes.co.za')
        self.assertTrue(addr.verified)
        self.assertTrue(addr.primary)
        r = self.client.post('/accounts/login/', follow=True,
                             data={'username': 'admin@blaqvibes.co.za',
                                   'password': 'youpwassword'})
        self.assertTrue(r.wsgi_request.user.is_authenticated)
        self.assertEqual(r.wsgi_request.user.username, 'admin')
        self.assertNotContains(r, 'unlock your 5')

    def test_anonymous_admin_url_redirects_to_login(self):
        r = self.client.get('/admin/dashboard/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r.url)

    def test_login_page_explains_local_demo_accounts(self):
        with self.settings(LOCAL_DEV=True, DEBUG=True):
            r = self.client.get('/accounts/login/')
        self.assertContains(r, 'admin@blaqvibes.co.za')
        self.assertContains(r, 'already confirmed')
        self.assertContains(r, 'nolo.ai')
        self.assertContains(r, 'blaq12345')
        self.assertContains(r, 'create_superadmin')

    def test_login_page_hides_demo_passwords_outside_local(self):
        with self.settings(LOCAL_DEV=False, DEBUG=False):
            r = self.client.get('/accounts/login/')
        self.assertNotContains(r, 'blaq12345')
        self.assertNotContains(r, 'create_superadmin')
