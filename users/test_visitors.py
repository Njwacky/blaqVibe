"""Visitor log — the "did anyone open the link?" tests.

The rules that matter:
- anonymous browsers are recorded (that is the whole point),
- asset/JSON/redirect/error requests are not (otherwise the numbers lie),
- the team's own visits and bots are recorded but excluded by default,
- only staff can read the table,
- the retention command actually deletes.
"""
from datetime import timedelta

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gallery.models import VisitorHit
from gallery.tests import make_user
from gallery.visitors import (
    BOT_PATTERN,
    daily_series,
    format_duration,
    ip_hash,
    parse_user_agent,
    referrer_parts,
    should_track,
    visitor_key,
)

HTML = 'text/html,application/xhtml+xml'
CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36'
)
IPHONE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
)


class FakeResponse:
    def __init__(self, status_code=200, **headers):
        self.status_code = status_code
        self._headers = headers

    def get(self, name, default=''):
        return self._headers.get(name, default)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-visitors')
class TrackingRuleTests(TestCase):
    def _request(self, path='/', method='GET', accept=HTML, ua=CHROME_UA, **extra):
        factory = RequestFactory()
        request = getattr(factory, method.lower())(path, **extra)
        request.META['HTTP_ACCEPT'] = accept
        request.META['HTTP_USER_AGENT'] = ua
        return request

    def test_rendered_html_page_is_a_visit(self):
        self.assertTrue(should_track(self._request('/'), FakeResponse(200)))

    def test_static_and_asset_requests_are_not_visits(self):
        self.assertFalse(should_track(self._request('/static/gallery/css/x.css'), FakeResponse(200)))
        self.assertFalse(should_track(self._request('/media/thumb.png'), FakeResponse(200)))
        self.assertFalse(should_track(self._request('/robots.txt'), FakeResponse(200)))
        self.assertFalse(should_track(self._request('/app/slug/file/logo.svg'), FakeResponse(200)))

    def test_downloads_and_non_html_bodies_are_not_visits(self):
        self.assertFalse(should_track(
            self._request('/app/slug/download/'), FakeResponse(200, **{'Content-Type': 'application/zip'})))
        self.assertFalse(should_track(
            self._request('/app/slug/download/'),
            FakeResponse(200, **{'Content-Type': 'text/html',
                                 'Content-Disposition': 'attachment; filename="x.zip"'})))
        self.assertTrue(should_track(
            self._request('/app/slug/'), FakeResponse(200, **{'Content-Type': 'text/html; charset=utf-8'})))

    def test_non_page_responses_are_not_visits(self):
        self.assertFalse(should_track(self._request('/'), FakeResponse(302)))
        self.assertFalse(should_track(self._request('/'), FakeResponse(404)))
        self.assertFalse(should_track(self._request('/'), FakeResponse(304)))
        self.assertFalse(should_track(self._request('/'), FakeResponse(500)))

    def test_json_and_post_are_not_visits(self):
        self.assertFalse(should_track(
            self._request('/api/v1/apps/', accept='application/json'), FakeResponse(200)))
        self.assertFalse(should_track(
            self._request('/accounts/login/', method='POST'), FakeResponse(200)))

    def test_admin_and_ops_paths_are_not_visits(self):
        for path in ('/admin/catch-all', '/blaq-admin-secure/', '/ops/brave/',
                     '/healthz', '/accounts/social/google/login/'):
            self.assertFalse(should_track(self._request(path), FakeResponse(200)), path)

    def test_missing_ua_is_treated_as_a_bot(self):
        parsed = parse_user_agent('')
        self.assertTrue(parsed['is_bot'])
        self.assertEqual(parsed['device'], 'Bot')

    def test_user_agent_labels(self):
        chrome = parse_user_agent(CHROME_UA)
        self.assertEqual((chrome['device'], chrome['browser'], chrome['os']),
                         ('Desktop', 'Chrome', 'Windows'))
        iphone = parse_user_agent(IPHONE_UA)
        self.assertEqual((iphone['device'], iphone['browser'], iphone['os']),
                         ('Phone', 'Safari', 'iOS'))
        self.assertTrue(BOT_PATTERN.search('Mozilla/5.0 (compatible; Googlebot/2.1)'))

    def test_visitor_key_never_contains_the_ip(self):
        key = visitor_key('41.13.1.2', CHROME_UA)
        self.assertNotIn('41.13.1.2', key)
        self.assertEqual(key, visitor_key('41.13.1.2', CHROME_UA))
        self.assertNotEqual(key, visitor_key('41.13.1.3', CHROME_UA))
        self.assertNotIn('41.13.1.2', ip_hash('41.13.1.2'))

    def test_proxy_ip_is_kept_only_as_an_unverified_hint(self):
        from gallery.visitors import forwarded_ip
        request = self._request('/', HTTP_X_FORWARDED_FOR='196.25.1.2, 10.0.0.1')
        self.assertEqual(forwarded_ip(request), '196.25.1.2')
        self.assertIsNone(forwarded_ip(self._request('/')))
        garbage = self._request('/', HTTP_X_FORWARDED_FOR='not-an-ip')
        self.assertIsNone(forwarded_ip(garbage))

    def test_referrer_is_blanked_for_internal_navigation(self):
        external = self._request('/', HTTP_REFERER='https://twitter.com/njwacky/status/1')
        self.assertEqual(referrer_parts(external),
                         ('https://twitter.com/njwacky/status/1', 'twitter.com'))
        internal = self._request('/', HTTP_REFERER='http://testserver/app/slug/')
        self.assertEqual(referrer_parts(internal), ('', ''))


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-visitors')
class MiddlewareWriteTests(TestCase):
    def test_anonymous_visit_is_recorded(self):
        self.client.get(reverse('feed'), HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA)
        hit = VisitorHit.objects.get(path=reverse('feed'))
        self.assertIsNone(hit.user, 'an anonymous visit must not invent a user')
        self.assertFalse(hit.is_bot)
        self.assertTrue(hit.is_first_visit)
        self.assertEqual(hit.ip, '127.0.0.1')
        self.assertEqual(hit.browser, 'Chrome')
        self.assertEqual(hit.device, 'Desktop')

    def test_forwarded_ip_is_recorded_next_to_the_real_one(self):
        self.client.get(reverse('feed'), HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA,
                        HTTP_X_FORWARDED_FOR='196.25.1.2')
        hit = VisitorHit.objects.get(path=reverse('feed'))
        self.assertEqual(hit.ip, '127.0.0.1')
        self.assertEqual(hit.forwarded_ip, '196.25.1.2')

    def test_second_visit_by_the_same_browser_is_not_new(self):
        for _ in range(2):
            self.client.get(reverse('feed'), HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA)
        rows = VisitorHit.objects.filter(path=reverse('feed')).order_by('created_at')
        self.assertEqual(rows.count(), 2)
        self.assertTrue(rows[0].is_first_visit)
        self.assertFalse(rows[1].is_first_visit)
        self.assertEqual(rows[0].visitor_key, rows[1].visitor_key)

    def test_signed_in_visit_links_the_account(self):
        user = make_user('visitor1')
        self.client.force_login(user)
        self.client.get(reverse('feed'), HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA)
        hit = VisitorHit.objects.get(path=reverse('feed'))
        self.assertEqual(hit.user, user)

    def test_team_visit_is_marked_internal(self):
        boss = make_user('visitboss', role='superadmin')
        self.client.force_login(boss)
        self.client.get(reverse('feed'), HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA)
        self.assertTrue(VisitorHit.objects.get(path=reverse('feed')).is_internal)

    def test_asset_request_writes_nothing(self):
        self.client.get('/static/gallery/css/base.css', HTTP_ACCEPT='text/css,*/*;q=0.1',
                        HTTP_USER_AGENT=CHROME_UA)
        self.assertEqual(VisitorHit.objects.count(), 0)

    def test_redirect_is_not_a_visit(self):
        # Anonymous POST to a login-required inbox answers 302 then 405/200;
        # the redirect hop itself must never become a "visit".
        response = self.client.get('/saved/', HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA)
        if response.status_code == 302:
            self.assertFalse(VisitorHit.objects.filter(path='/saved/').exists())

    def test_tracking_can_be_switched_off(self):
        with override_settings(BLAQVIBES_TRACK_VISITORS=False):
            self.client.get(reverse('feed'), HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA)
        self.assertEqual(VisitorHit.objects.count(), 0)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-visitors')
class TimeOnSiteTests(TestCase):
    """Time is measured from page-view timestamps — no heartbeat, no JS."""

    def _visit(self, path):
        return self.client.get(path, HTTP_ACCEPT=HTML, HTTP_USER_AGENT=CHROME_UA)

    def test_duration_labels(self):
        self.assertEqual(format_duration(None), '')
        self.assertEqual(format_duration(0), '0s')
        self.assertEqual(format_duration(38), '38s')
        self.assertEqual(format_duration(102), '1m 42s')
        self.assertEqual(format_duration(3600 + 4 * 60), '1h 04m')

    def test_consecutive_pages_form_one_visit_and_close_the_previous_page(self):
        self._visit(reverse('feed'))
        first = VisitorHit.objects.get(path=reverse('feed'))
        self._visit(reverse('discover'))
        second = VisitorHit.objects.get(path=reverse('discover'))

        first.refresh_from_db()
        self.assertEqual(first.session_id, second.session_id)
        self.assertAlmostEqual(
            second.session_started_at, first.created_at, delta=timedelta(seconds=2))
        self.assertIsNotNone(first.dwell_seconds,
                            'the page they left must get a measured dwell time')
        self.assertIsNone(second.dwell_seconds,
                          'the page they are on is not over yet')
        self.assertEqual(first.dwell_label(),
                         format_duration(first.dwell_seconds))

    def test_a_long_gap_starts_a_new_visit(self):
        self._visit(reverse('feed'))
        first = VisitorHit.objects.get(path=reverse('feed'))
        old = timezone.now() - timedelta(hours=2)
        VisitorHit.objects.filter(pk=first.pk).update(
            created_at=old, session_started_at=old)

        self._visit(reverse('discover'))
        second = VisitorHit.objects.get(path=reverse('discover'))
        first.refresh_from_db()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertIsNone(first.dwell_seconds,
                          'a two-hour gap must not be recorded as time on page')
        self.assertAlmostEqual(
            second.session_started_at, second.created_at, delta=timedelta(seconds=2))

    def test_session_idle_window_is_configurable(self):
        self._visit(reverse('feed'))
        first = VisitorHit.objects.get(path=reverse('feed'))
        VisitorHit.objects.filter(pk=first.pk).update(
            created_at=timezone.now() - timedelta(minutes=45))
        with override_settings(VISITOR_SESSION_IDLE_MINUTES=90):
            self._visit(reverse('discover'))
        second = VisitorHit.objects.get(path=reverse('discover'))
        self.assertEqual(first.session_id, second.session_id)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-visitors')
class VisitorDashboardTests(TestCase):
    def setUp(self):
        self.admin = make_user('visitadmin', role='admin')
        self.member = make_user('visitmember')

    def _hit(self, **kwargs):
        defaults = {
            'path': '/app/demo/',
            'ip': '41.13.1.2',
            'visitor_key': 'k1',
            'user_agent': CHROME_UA,
            'device': 'Desktop',
            'browser': 'Chrome',
            'os': 'Windows',
        }
        defaults.update(kwargs)
        return VisitorHit.objects.create(**defaults)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse('visitor_log'))
        self.assertEqual(response.status_code, 302)

    def test_plain_member_is_forbidden(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('visitor_log')).status_code, 403)

    def test_admin_sees_anonymous_visitors(self):
        self._hit()
        self.client.force_login(self.admin)
        response = self.client.get(reverse('visitor_log'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not registered')
        self.assertContains(response, '/app/demo/')
        self.assertContains(response, '41.13.1.2')

    def test_bots_and_team_are_hidden_by_default_but_toggleable(self):
        self._hit(path='/bot-page/')
        VisitorHit.objects.filter(path='/bot-page/').update(is_bot=True)
        self._hit(path='/team-page/', is_internal=True)
        self.client.force_login(self.admin)

        default = self.client.get(reverse('visitor_log'))
        self.assertNotContains(default, '/bot-page/')
        self.assertNotContains(default, '/team-page/')

        everything = self.client.get(reverse('visitor_log'), {'bots': '1', 'team': '1'})
        self.assertContains(everything, '/bot-page/')
        self.assertContains(everything, '/team-page/')

    def test_path_and_ip_filters_narrow_the_table(self):
        self._hit(path='/app/one/')
        self._hit(path='/app/two/', ip='41.13.9.9')
        self.client.force_login(self.admin)

        by_path = self.client.get(reverse('visitor_log'), {'path': '/app/one/'})
        self.assertContains(by_path, '/app/one/')
        self.assertNotContains(by_path, '/app/two/')

        by_ip = self.client.get(reverse('visitor_log'), {'ip': '41.13.9.9'})
        self.assertContains(by_ip, '/app/two/')
        self.assertNotContains(by_ip, '/app/one/')

    def test_time_columns_and_cards_are_rendered(self):
        now = timezone.now()
        finished = self._hit(path='/app/timed/', dwell_seconds=42,
                             session_started_at=now - timedelta(minutes=12),
                             session_id='sess-1')
        # Not live: they left this page twelve minutes ago.
        VisitorHit.objects.filter(pk=finished.pk).update(
            created_at=now - timedelta(minutes=10))
        self._hit(path='/app/live/', visitor_key='k2',
                  session_started_at=now - timedelta(seconds=30),
                  session_id='sess-2')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('visitor_log'))
        self.assertContains(response, '42s on page')
        self.assertContains(response, '2m 00s into the visit')
        self.assertContains(response, 'on page now')
        self.assertContains(response, 'ON SITE NOW')
        self.assertContains(response, 'AVG TIME ON PAGE')
        self.assertContains(response, 'AVG TIME ON VISIT')

    def test_old_rows_fall_outside_the_window(self):
        old = self._hit(path='/ancient/')
        VisitorHit.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=40))
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('visitor_log')), '/ancient/')
        self.assertContains(
            self.client.get(reverse('visitor_log'), {'days': '90'}), '/ancient/')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-visitors')
class VisitorRetentionTests(TestCase):
    def test_daily_series_zero_fills_and_counts_uniques(self):
        now = timezone.now()
        VisitorHit.objects.create(path='/', visitor_key='a', ip='41.0.0.1')
        VisitorHit.objects.create(path='/', visitor_key='a', ip='41.0.0.1')
        VisitorHit.objects.create(path='/', visitor_key='b', ip='41.0.0.2')
        rows = daily_series(VisitorHit.objects.all(), days=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1][1], 3)
        self.assertEqual(rows[-1][2], 2)
        self.assertEqual(rows[0][1:], (0, 0))
        self.assertTrue(all(row[0] <= now.date() for row in rows))

    def test_prune_command_deletes_only_stale_rows(self):
        from django.core.management import call_command
        from io import StringIO

        stale = VisitorHit.objects.create(path='/old/')
        VisitorHit.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=100))
        fresh = VisitorHit.objects.create(path='/new/')

        out = StringIO()
        call_command('prune_visitor_hits', '--days', '90', '--dry-run', stdout=out)
        self.assertIn('Would delete 1', out.getvalue())
        self.assertEqual(VisitorHit.objects.count(), 2)

        call_command('prune_visitor_hits', '--days', '90', stdout=out)
        self.assertFalse(VisitorHit.objects.filter(pk=stale.pk).exists())
        self.assertTrue(VisitorHit.objects.filter(pk=fresh.pk).exists())
