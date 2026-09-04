"""Regression tests for the SECURITY_AUDIT_FINDINGS.md fixes.

Each class maps to a numbered finding in the audit report. They model the
existing conventions in gallery/tests.py / users/tests.py:
  - `make_user` / `make_project` / `make_category` / `make_zip_file` helpers,
  - `@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')`,
  - locmem-pinned CACHES when a rate limit itself is under test.
"""
import json
import os
import subprocess
import sys
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.conf import settings

from gallery.models import AppReport, AppVersion, PullRequest, Star

# Reuse the shared helpers from gallery.tests so the regression suite
# exercises the exact same fixtures the rest of the app tests use.
from gallery.tests import make_category, make_project, make_user, make_zip_file

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class PullRequestVisibilityRegressionTests(TestCase):
    """Finding #1 — PR pages must not leak unpublished forks to strangers."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer')          # fork owner / PR author
        self.stranger = make_user('stranger')
        self.mod = make_user('mod', role='moderator')

        self.target = make_project(self.owner, self.cat, title='Target', star_cost=0)
        self.target.zip_file.save('target.zip', make_zip_file({'app.py': 'print("old")\n'}), save=True)

        self.fork = make_project(self.buyer, self.cat, title='Fork', forked_from=self.target,
                                 status='pending', star_cost=0)
        self.fork.zip_file.save('fork.zip', make_zip_file({'app.py': 'print("new")\n'}), save=True)

        self.pr = PullRequest.objects.create(
            source=self.fork, target=self.target, author=self.buyer,
            title='Add new app.py', status='open',
        )

        self.pending_target = make_project(self.owner, self.cat, title='Pending target', status='pending')

    def _pr_url(self):
        return f'/app/{self.target.slug}/prs/{self.pr.id}/view/'

    def test_anonymous_cannot_read_pending_fork_diff(self):
        response = self.client.get(self._pr_url())
        self.assertEqual(response.status_code, 404)

    def test_stranger_cannot_read_pending_fork_diff(self):
        self.client.login(username='stranger', password='pass12345')
        response = self.client.get(self._pr_url())
        self.assertEqual(response.status_code, 404)

    def test_fork_owner_can_read_own_pending_fork_diff(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(self._pr_url())
        self.assertEqual(response.status_code, 200)

    def test_target_owner_can_review_pr_from_pending_fork(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(self._pr_url())
        self.assertEqual(response.status_code, 200)

    def test_moderator_can_read_pending_fork_diff(self):
        self.client.login(username='mod', password='pass12345')
        response = self.client.get(self._pr_url())
        self.assertEqual(response.status_code, 200)

    def test_anonymous_pr_list_of_pending_target_is_404(self):
        response = self.client.get(f'/app/{self.pending_target.slug}/prs/')
        self.assertEqual(response.status_code, 404)

    def test_anonymous_pr_list_of_published_target_is_200(self):
        response = self.client.get(f'/app/{self.target.slug}/prs/')
        self.assertEqual(response.status_code, 200)

    def test_anonymous_fork_network_of_pending_root_is_404(self):
        response = self.client.get(f'/app/{self.pending_target.slug}/forks/')
        self.assertEqual(response.status_code, 404)

    def test_anonymous_fork_network_of_published_root_is_200(self):
        response = self.client.get(f'/app/{self.target.slug}/forks/')
        self.assertEqual(response.status_code, 200)

    def test_fork_network_climb_to_pending_original_404_for_stranger(self):
        # A published fork can point at an original that has since re-queued
        # (pending). fork_network follows the forked_from chain up to that
        # root; a stranger must not be handed the pending original's metadata
        # — it is the same "guessed slug must not be confirmable" rule as the
        # direct pending-root case.
        original = make_project(self.owner, self.cat, title='Now-pending original', status='pending')
        pubfork = make_project(self.buyer, self.cat, title='Published fork',
                               forked_from=original, status='published')
        response = self.client.get(f'/app/{pubfork.slug}/forks/')
        self.assertEqual(response.status_code, 404)

    def test_fork_network_climb_to_pending_original_200_for_original_owner(self):
        original = make_project(self.owner, self.cat, title='Now-pending original', status='pending')
        pubfork = make_project(self.buyer, self.cat, title='Published fork',
                               forked_from=original, status='published')
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{pubfork.slug}/forks/')
        self.assertEqual(response.status_code, 200)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ProfileStarsVisibilityRegressionTests(TestCase):
    """Related object-lookup leak — a public profile's Stars tab must not
    reveal vibes that have since gone non-public (pending/quarantined/removed).

    A star can only be cast on a published vibe (toggle_star), but that vibe
    can later be re-queued or removed. Reading the profile with ?tab=stars has
    to honour the same visibility rule as every other content read.
    """

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.stranger = make_user('stranger')
        # Realistic lifecycle: the vibe was once published (so it could be
        # starred) and has since been re-queued — status flips to 'pending'.
        self.pending = make_project(self.owner, self.cat, title='Secret pending vibe', star_cost=0)
        self.pending.zip_file.save('pending.zip', make_zip_file({'app.py': 'x=1\n'}), save=True)
        Star.objects.create(user=self.owner, project=self.pending)
        self.pending.status = 'pending'
        self.pending.save(update_fields=['status'])

    def test_stranger_profile_stars_hides_pending_vibe(self):
        response = self.client.get(f'/u/{self.owner.username}/?tab=stars')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret pending vibe')

    def test_owner_profile_stars_still_shows_own_pending_vibe(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/u/{self.owner.username}/?tab=stars')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Secret pending vibe')

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class DownloadAndReportGateRegressionTests(TestCase):
    """Finding #6 — download_version / report_vibe must respect status."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer')
        self.published = make_project(self.owner, self.cat, title='Live', status='published')
        self.published.zip_file.save('live.zip', make_zip_file({'a.py': 'x=1\n'}), save=True)
        self.pending = make_project(self.owner, self.cat, title='Pending', status='pending')
        self.pending.zip_file.save('pending.zip', make_zip_file({'b.py': 'y=2\n'}), save=True)
        self.version = AppVersion.objects.create(
            project=self.pending, version='1.0.0',
            zip_file=make_zip_file({'old.py': 'z=3\n'}, name='old.zip'),
        )

    def test_stranger_cannot_download_version_of_pending_project(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/app/{self.pending.slug}/versions/{self.version.id}/download/')
        self.assertEqual(response.status_code, 404)

    def test_owner_can_download_version_of_own_pending_project(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{self.pending.slug}/versions/{self.version.id}/download/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')

    def test_report_pending_project_is_404(self):
        response = self.client.post(f'/app/{self.pending.slug}/report/', {'reason': 'spam'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(AppReport.objects.filter(project=self.pending).exists())

    def test_report_published_project_still_works(self):
        response = self.client.post(f'/app/{self.published.slug}/report/', {'reason': 'spam'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AppReport.objects.filter(project=self.published, reason='spam').exists())

def _unique_locmem(name):
    """Fresh locmem cache backend for one test class (see AuthRateLimit below)."""
    from django.core.cache.backends.locmem import LocMemCache  # noqa: F401  (import-string target)

    return f'{name}-{uuid.uuid4().hex}'


class AuthRateLimitRegressionTests(TestCase):
    """Finding #2 — login and password-reset are rate-limited per IP."""

    # Rate-limit tests pin the locmem cache with a unique name per test class.
    # A shared LOCATION (e.g. 'test-ratelimit') makes every class's counters
    # collide in ONE process-wide dict: whichever rate-limit test runs first
    # leaves residue behind that makes a later class's burst trip early OR
    # never reach its ceiling — both flaky. test_security_scenarios hit this
    # and invented unique aliases (ab-default/ab-ratelimit); a uuid does the
    # same job without hand-managed names.
    # NOTE: django-ratelimit reads settings.RATELIMIT_USE_CACHE through the
    # 'ratelimit' cache *alias* (caches['ratelimit']), so every override still
    # spells the alias 'ratelimit'; only the backend's LOCATION differs.
    _ratelimit_cache_name = 'auth-sec-ratelimit'
    _default_cache_name = 'auth-sec-default'

    def setUp(self):
        self._caches = override_settings(
            RATELIMIT_ENABLE=True,
            RATELIMIT_USE_CACHE='ratelimit',
            CACHES={
                'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                            'LOCATION': _unique_locmem(self._default_cache_name)},
                'ratelimit': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                              'LOCATION': _unique_locmem(self._ratelimit_cache_name)},
            },
        )
        self._caches.enable()
        self.addCleanup(self._caches.disable)
        make_user('someone')  # real user so a good login is meaningful

    def test_login_post_is_rate_limited_at_20_per_minute(self):
        # Rate is 20/m on POST only. django-ratelimit counts per wall-clock
        # window (core._get_window jitters the edge by the IP), so a burst of
        # slow POSTs can straddle a window boundary and get a fresh bucket
        # mid-loop — with real time, "the 21st POST is blocked" is a coin
        # flip (observed 1-in-8 failure on this very test). Pin the clock for
        # the burst so all 21 land in ONE window; then the 21st must 403 via
        # handler403/safe_403, the codebase's established block=True pattern.
        with patch('django_ratelimit.core.time.time', return_value=1_900_000_000):
            last = None
            for _ in range(21):
                last = self.client.post('/accounts/login/', {'username': 'someone', 'password': 'wrong'})
        self.assertEqual(last.status_code, 403)

    def test_login_get_is_not_rate_limited(self):
        # GET must stay open so a bot cannot 403 the login form itself out
        # from under a classroom/NAT. GET is not counted (method='POST').
        for _ in range(25):
            response = self.client.get('/accounts/login/')
            self.assertEqual(response.status_code, 200)

    def test_password_reset_post_is_rate_limited_at_10_per_minute(self):
        # Same window-pinning rationale as the login test: 10/m POST ceiling,
        # 11th POST in the same window must 403.
        with patch('django_ratelimit.core.time.time', return_value=1_900_000_000):
            last = None
            for _ in range(11):
                last = self.client.post('/accounts/password_reset/', {'email': 'someone@test.com'})
        self.assertEqual(last.status_code, 403)


class CspReportRateLimitRegressionTests(TestCase):
    """Finding #8 — unauthenticated csp-report POST flood is bounded."""

    # Unique locmem LOCATION per class — see AuthRateLimitRegressionTests.
    _ratelimit_cache_name = 'csp-sec-ratelimit'
    _default_cache_name = 'csp-sec-default'

    def setUp(self):
        self._caches = override_settings(
            RATELIMIT_ENABLE=True,
            RATELIMIT_USE_CACHE='ratelimit',
            CACHES={
                'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                            'LOCATION': _unique_locmem(self._default_cache_name)},
                'ratelimit': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                              'LOCATION': _unique_locmem(self._ratelimit_cache_name)},
            },
        )
        self._caches.enable()
        self.addCleanup(self._caches.disable)

    def test_csp_report_accepts_legitimate_report(self):
        response = self.client.post(
            '/csp-report/',
            data=json.dumps({'csp-report': {'document-uri': 'https://example.com/'}}),
            content_type='application/csp-report',
        )
        self.assertEqual(response.status_code, 204)

    def test_csp_report_flood_is_rate_limited(self):
        # Pinned clock keeps all 61 posts in one window (see the auth tests):
        # with real time a 61-POST loop can straddle a window boundary and
        # legitimately end on 200 even though every window stayed ≤ 60/m.
        with patch('django_ratelimit.core.time.time', return_value=1_900_000_000):
            last = None
            for _ in range(61):  # rate is 60/m
                last = self.client.post('/csp-report/', data='{}', content_type='application/json')
        self.assertEqual(last.status_code, 403)

@override_settings(RATELIMIT_ENABLE=False)
class ReadinessLeakRegressionTests(TestCase):
    """Finding #5 — /readyz must not echo DB/Redis exception strings."""

    def test_readyz_db_failure_returns_static_label_only(self):
        secret = 'connect db.internal:5432 user=blaq password=hunter2'
        with patch('gallery.health.connection.cursor', side_effect=Exception(secret)):
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.content)
        self.assertEqual(body['checks']['database']['detail'], 'unavailable')
        raw = response.content.decode()
        self.assertNotIn('db.internal', raw)
        self.assertNotIn('hunter2', raw)
        self.assertNotIn('5432', raw)

class SecureProxyHeaderDefaultsTests(SimpleTestCase):
    """Finding #3 — client X-Forwarded-Proto is trusted only behind a proxy.

    SECURE_PROXY_SSL_HEADER is decided at settings-import time from the
    DJANGO_BEHIND_TLS_PROXY flag (plus the preview host), so the assertion
    re-imports settings in a subprocess with a controlled environment. The
    default docker-compose posture (no proxy) must NOT trust the header.
    """

    def _import_posture(self, behind):
        code = (
            "from django.conf import settings\n"
            "print(settings.SECURE_PROXY_SSL_HEADER is None, "
            "settings.USE_X_FORWARDED_HOST, settings.USE_X_FORWARDED_PORT)"
        )
        env = dict(os.environ)
        env.update({
            'DJANGO_SETTINGS_MODULE': 'blaqvibes.settings',
            'DJANGO_LOCAL_DEV': '1',
            'SECRET_KEY': 'test-secret',
            # Neutralise the preview host so we test the flag alone.
            'E2B_SANDBOX': '0',
            'DJANGO_PREVIEW': '0',
            'DJANGO_BEHIND_TLS_PROXY': behind,
        })
        out = subprocess.check_output(
            [sys.executable, '-c', code],
            cwd=str(settings.BASE_DIR),
            env=env,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        return out.decode().strip().splitlines()[-1].split()

    def test_without_proxy_forwarded_proto_is_untrusted(self):
        header_is_none, xfh, xfp = self._import_posture('0')
        self.assertEqual(header_is_none, 'True')
        self.assertEqual(xfh, 'False')
        self.assertEqual(xfp, 'False')

    def test_behind_proxy_forwarded_proto_is_trusted(self):
        header_is_none, xfh, xfp = self._import_posture('1')
        self.assertEqual(header_is_none, 'False')
        self.assertEqual(xfh, 'True')
        self.assertEqual(xfp, 'True')
