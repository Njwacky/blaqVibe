"""Admin dashboard chart tests.

Two layers:
- unit tests for the SVG builders (empty-state honesty, XSS escaping),
- view tests for the dashboard (role gating, chart presence, and the
  data rules: only append-only logs get charted).
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from gallery.models import AppProject, CloneEvent, ScanJob, Trade
from gallery.tests import make_category, make_project, make_user

from users.charts import daily_bars_chart, h_bars_chart


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-admin', SEED_DEMO=False)
class ChartBuilderTests(TestCase):
    def test_daily_bars_all_zero_returns_none(self):
        from datetime import date
        days = [date(2026, 8, i) for i in range(1, 8)]
        chart = daily_bars_chart('label', days, [{'name': 'x', 'color': '#000', 'values': [0] * 7}])
        self.assertIsNone(chart)

    def test_daily_bars_draws_nonzero(self):
        from datetime import date
        days = [date(2026, 8, i) for i in range(1, 8)]
        chart = daily_bars_chart('label', days, [{'name': 'x', 'color': '#000', 'values': [0, 0, 3, 0, 1, 0, 0]}])
        self.assertIsNotNone(chart)
        self.assertIn('<svg', chart)
        self.assertIn('<rect', chart)
        self.assertIn('aria-label="label"', chart)

    def test_h_bars_all_zero_returns_none(self):
        chart = h_bars_chart('label', [{'label': 'a', 'value': 0}])
        self.assertIsNone(chart)

    def test_h_bars_escapes_user_text(self):
        chart = h_bars_chart('label', [{'label': '<script>alert(1)</script>', 'value': 5}])
        self.assertIn('&lt;script&gt;', chart)
        self.assertNotIn('<script>alert(1)</script>', chart)

    def test_h_bars_links(self):
        chart = h_bars_chart('label', [{'label': 'Vibe', 'value': 5}], hrefs=['/app/vibe/'])
        self.assertIn('<a href="/app/vibe/">', chart)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-admin', SEED_DEMO=False)
class AdminDashboardViewTests(TestCase):
    def setUp(self):
        self.admin = make_user('dashadmin', **{'role': 'admin'})
        self.owner = make_user('dashowner')
        self.buyer = make_user('dashbuyer')
        self.cat = make_category()
        self.project = make_project(self.owner, self.cat, slug='dash-vibe', title='Dash Vibe')

    def test_anonymous_and_plain_users_are_denied(self):
        resp = self.client.get('/admin/dashboard/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.url)
        self.assertIn('next=', resp.url)
        self.client.login(username='dashbuyer', password='pass12345')
        resp = self.client.get('/admin/dashboard/')
        self.assertEqual(resp.status_code, 403)

    def test_admin_sees_dashboard_with_stats(self):
        self.client.login(username='dashadmin', password='pass12345')
        resp = self.client.get('/admin/dashboard/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Admin Dashboard', body)
        self.assertIn('TOTAL VIBES', body)
        self.assertIn('QUARANTINE RATE', body)
        self.assertIn('No clones logged yet', body)
        self.assertIn('No trades yet.', body)

    def test_dashboard_charts_real_events(self):
        now = timezone.now()
        CloneEvent.objects.create(project=self.project, user=self.buyer, source='git')
        CloneEvent.objects.create(project=self.project, user=None, source='zip', ip_hash='abc')
        Trade.objects.create(buyer=self.buyer, seller=self.owner, project=self.project, cost=3)
        Trade.objects.create(buyer=self.buyer, seller=self.owner, project=self.project, cost=5)
        ScanJob.objects.create(project=self.project, status='clean', updated_at=now - timedelta(days=1))
        self.project.stars = 42
        self.project.save(update_fields=['stars'])

        self.client.login(username='dashadmin', password='pass12345')
        resp = self.client.get('/admin/dashboard/')
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('<svg', body)
        self.assertIn('CLONES / DAY', body)
        self.assertIn('TRADES / DAY', body)
        self.assertIn('★ VOLUME / DAY', body)
        self.assertIn('SCAN OUTCOMES', body)
        self.assertIn('Dash Vibe', body)
        self.assertIn('/app/dash-vibe/', body)

    def test_quarantine_rate_math(self):
        ScanJob.objects.create(project=self.project, status='clean')
        other = make_project(self.owner, self.cat, slug='dash-bad', title='Dash Bad')
        ScanJob.objects.create(project=other, status='quarantined')
        yet_another = make_project(self.owner, self.cat, slug='dash-failed', title='Dash Failed')
        ScanJob.objects.create(project=yet_another, status='failed')
        queued = make_project(self.owner, self.cat, slug='dash-queued', title='Dash Queued')
        ScanJob.objects.create(project=queued, status='queued')

        self.client.login(username='dashadmin', password='pass12345')
        resp = self.client.get('/admin/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('50.0%', resp.content.decode())
