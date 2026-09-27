"""CSRF cookie / preview-iframe regressions.
"""
from django.conf import settings
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


class PreviewSessionCookieTests(TestCase):
    """The session cookie must survive the preview iframe, not just the CSRF one.

    The Arena live preview is https://{port}-{sandbox}.e2b.app framed inside
    another site, so every cookie it sets is a *third-party* cookie. Browsers
    now block those unless they carry `Partitioned` (CHIPS) alongside
    `SameSite=None; Secure` — SameSite=None on its own is no longer enough.

    These tests drive a real login POST through the whole middleware stack
    rather than a synthetic view that sets its own cookie, because the bug they
    pin was an ordering one: PreviewEmbedMiddleware sat further in than
    SessionMiddleware, so its rewrite ran while `csrftoken` was already on the
    response but `sessionid` was not yet — the CSRF cookie came back Partitioned
    and the session cookie never did. Sign-in then answered 302 -> `/`, the
    browser dropped the unpartitioned session cookie in the cross-site frame,
    and the feed rendered signed-out with every later POST landing on the 403
    page. The synthetic tests above could not see this: they hand the
    middleware a response that already carries both cookies.
    """

    PREVIEW_HOST = '8000-abc123.e2b.app'
    PUBLIC_HOST = 'blaqvibes.co.za'

    def setUp(self):
        self.user = User.objects.create_user(
            'admin', 'admin@blaqvibes.co.za', PW,
        )
        self.client = self.client_class(enforce_csrf_checks=True)

    def _login(self, host):
        """GET the form, then POST it, as `host` over HTTPS.

        HTTP_HOST (not an absolute URL) is what reaches `request.get_host()`;
        the test client does not parse a scheme+host out of the path, and
        `host_needs_embed_cookies` keys off exactly that.
        """
        origin = f'https://{host}'
        get = self.client.get(
            '/accounts/login/', secure=True, HTTP_HOST=host,
        )
        self.assertEqual(get.status_code, 200)
        return self.client.post(
            '/accounts/login/',
            {
                'username': 'admin@blaqvibes.co.za',
                'password': PW,
                'csrfmiddlewaretoken': str(get.context['csrf_token']),
            },
            secure=True,
            HTTP_HOST=host,
            headers={'origin': origin},
        )

    def test_embed_middleware_is_outermost_so_it_sees_every_cookie(self):
        """The ordering contract itself — the cheapest guard against a re-move.

        `load_middleware` wraps in reverse, so index 0 is the OUTERMOST layer
        and the one whose response-phase code runs last, after SessionMiddleware
        and CsrfViewMiddleware have written their cookies.
        """
        self.assertEqual(
            settings.MIDDLEWARE[0],
            'gallery.middleware.PreviewEmbedMiddleware',
        )

    def test_login_redirects_to_the_feed(self):
        response = self._login(self.PREVIEW_HOST)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_login_sets_a_partitioned_session_cookie(self):
        """A session cookie a cross-site browser will actually keep."""
        response = self._login(self.PREVIEW_HOST)
        morsel = response.cookies.get(settings.SESSION_COOKIE_NAME)
        self.assertIsNotNone(morsel, 'login must set a session cookie')
        self.assertTrue(morsel['partitioned'])
        self.assertTrue(morsel['secure'])
        self.assertEqual(morsel['samesite'], 'None')
        # The serialized header, not just the morsel: that is what the browser
        # parses (wsgi.py builds Set-Cookie from response.cookies at send time).
        self.assertIn('Partitioned', morsel.OutputString())

    def test_login_sets_a_partitioned_csrf_cookie(self):
        response = self._login(self.PREVIEW_HOST)
        morsel = response.cookies.get(settings.CSRF_COOKIE_NAME)
        self.assertIsNotNone(morsel)
        self.assertTrue(morsel['partitioned'])
        self.assertTrue(morsel['secure'])
        self.assertEqual(morsel['samesite'], 'None')

    def test_signed_in_session_survives_the_next_request(self):
        """The user-visible symptom: land on `/` actually signed in."""
        response = self._login(self.PREVIEW_HOST)
        morsel = response.cookies.get(settings.SESSION_COOKIE_NAME)
        self.assertIsNotNone(morsel)
        self.assertTrue(morsel['partitioned'])

        # Replay only what a cross-site browser would have been allowed to
        # store, and check the next page believes it.
        follow_up = self.client.get(
            '/',
            secure=True,
            HTTP_HOST=self.PREVIEW_HOST,
            HTTP_COOKIE=f'{settings.SESSION_COOKIE_NAME}={morsel.value}',
        )
        self.assertEqual(follow_up.status_code, 200)
        self.assertTrue(follow_up.wsgi_request.user.is_authenticated)
        self.assertEqual(follow_up.wsgi_request.user.username, 'admin')

    def test_public_host_never_gets_partitioned_cookies(self):
        """The rewrite is for the preview host only — never the real site.

        Run under the *most* aggressive posture on purpose (PREVIEW and the
        partition flag both on) so what is being proved is the host guard in
        `host_needs_embed_cookies`, not whichever way this environment happens
        to have the cookie settings resolved.
        """
        with override_settings(PREVIEW=True, PARTITION_EMBED_COOKIES=True):
            response = self._login(self.PUBLIC_HOST)
        self.assertEqual(response.status_code, 302)
        morsel = response.cookies.get(settings.SESSION_COOKIE_NAME)
        self.assertIsNotNone(morsel)
        self.assertFalse(morsel['partitioned'])
        self.assertNotIn('Partitioned', morsel.OutputString())
        # Framing stays forbidden on the public site: only the e2b preview host
        # has X-Frame-Options removed.
        self.assertIn('X-Frame-Options', response.headers)
