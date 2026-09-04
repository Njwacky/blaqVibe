"""CSRF cookie / preview-iframe regressions.
"""
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from blaqvibes.settings import cookie_security, csrf_trusted_origins
from gallery.middleware import PreviewEmbedMiddleware, host_needs_embed_cookies

User = get_user_model()
PW = 'Admin@BlaqVibe2026'

class CookieSecurityHelperTests(SimpleTestCase):
    def test_preview_is_samesite_none_secure(self):
        flags = cookie_security(production=False, preview=True)
        self.assertTrue(flags['CSRF_COOKIE_SECURE'])
        self.assertTrue(flags['SESSION_COOKIE_SECURE'])
        self.assertEqual(flags['CSRF_COOKIE_SAMESITE'], 'None')
        self.assertEqual(flags['SESSION_COOKIE_SAMESITE'], 'None')
        self.assertTrue(flags['partition_cookies'])

    def test_preview_wins_over_production_flags(self):
        flags = cookie_security(production=True, preview=True)
        self.assertEqual(flags['CSRF_COOKIE_SAMESITE'], 'None')
        self.assertTrue(flags['partition_cookies'])

    def test_production_stays_lax_secure(self):
        flags = cookie_security(production=True, preview=False)
        self.assertTrue(flags['CSRF_COOKIE_SECURE'])
        self.assertEqual(flags['CSRF_COOKIE_SAMESITE'], 'Lax')
        self.assertFalse(flags['partition_cookies'])

    def test_local_http_is_not_secure(self):
        flags = cookie_security(production=False, preview=False)
        self.assertFalse(flags['CSRF_COOKIE_SECURE'])
        self.assertEqual(flags['CSRF_COOKIE_SAMESITE'], 'Lax')

    def test_preview_always_trusts_e2b_origin(self):
        origins = csrf_trusted_origins(
            'https://blaqvibes.co.za', preview=True, local=False,
        )
        self.assertIn('https://*.e2b.app', origins)
        self.assertIn('https://blaqvibes.co.za', origins)
        self.assertEqual(origins.count('https://*.e2b.app'), 1)

    def test_production_list_is_not_silently_widened(self):
        origins = csrf_trusted_origins(
            'https://blaqvibes.co.za,https://www.blaqvibes.co.za',
            preview=False, local=False,
        )
        self.assertEqual(
            origins,
            ['https://blaqvibes.co.za', 'https://www.blaqvibes.co.za'],
        )

class PreviewEmbedMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_e2b_host_needs_embed_cookies(self):
        request = self.factory.get('/accounts/login/', HTTP_HOST='8000-abc.e2b.app')
        with override_settings(PREVIEW=False, PARTITION_EMBED_COOKIES=False):
            self.assertTrue(host_needs_embed_cookies(request))

    def test_production_host_does_not(self):
        request = self.factory.get('/accounts/login/', HTTP_HOST='blaqvibes.co.za')
        with override_settings(PREVIEW=False, PARTITION_EMBED_COOKIES=False):
            self.assertFalse(host_needs_embed_cookies(request))

    def test_rewrites_csrf_cookie_on_e2b_host(self):
        def view(request):
            from django.http import HttpResponse
            response = HttpResponse('ok')
            response.set_cookie('csrftoken', 'secret', samesite='Lax', secure=False)
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response

        mw = PreviewEmbedMiddleware(view)
        request = self.factory.get('/accounts/login/', HTTP_HOST='8000-abc.e2b.app')
        with override_settings(PREVIEW=False, PARTITION_EMBED_COOKIES=False):
            response = mw(request)
        morsel = response.cookies['csrftoken']
        self.assertEqual(morsel['samesite'], 'None')
        self.assertTrue(morsel['secure'])
        self.assertTrue(morsel['partitioned'])
        self.assertNotIn('X-Frame-Options', response.headers)

    def test_production_host_never_rewrites_even_if_partition_flag(self):
        def view(request):
            from django.http import HttpResponse
            response = HttpResponse('ok')
            response.set_cookie('csrftoken', 'secret', samesite='Lax', secure=True)
            response['X-Frame-Options'] = 'DENY'
            return response

        mw = PreviewEmbedMiddleware(view)
        request = self.factory.get('/accounts/login/', HTTP_HOST='blaqvibes.co.za')
        with override_settings(PREVIEW=True, PARTITION_EMBED_COOKIES=True):
            response = mw(request)
        morsel = response.cookies['csrftoken']
        self.assertEqual(morsel['samesite'], 'Lax')
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertFalse(morsel['partitioned'])

class CsrfEnforcedLoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'admin', 'admin@blaqvibes.co.za', PW,
        )
        self.user.profile.role = 'superadmin'
        self.user.profile.email_verified = True
        self.user.profile.save()
        self.client = self.client_class(enforce_csrf_checks=True)

    def test_login_page_sets_csrf_cookie(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('csrftoken', response.cookies)
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_login_post_with_cookie_and_token_succeeds(self):
        get = self.client.get('/accounts/login/')
        token = get.context['csrf_token']
        response = self.client.post('/accounts/login/', {
            'username': 'admin@blaqvibes.co.za',
            'password': PW,
            'csrfmiddlewaretoken': str(token),
        }, follow=True)
        self.assertNotEqual(response.status_code, 403)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_login_post_without_cookie_is_403_not_exempt(self):
        # Fresh client: no csrftoken cookie.
        bare = self.client_class(enforce_csrf_checks=True)
        response = bare.post('/accounts/login/', {
            'username': 'admin@blaqvibes.co.za',
            'password': PW,
            'csrfmiddlewaretoken': 'a' * 64,
        })
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'security cookie', status_code=403)
        self.assertContains(response, reverse('login'), status_code=403)
