"""Tests for the report triage continuum: create → staff badge → resolve.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gallery.models import AppProject, AppReport, Notification, ScanJob, Trade
from gallery.tests import make_category, make_project, make_user
from users.models import AdminLog

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ReportQueueAccessTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.mod = make_user('mod', role='moderator')
        self.admin = make_user('adminuser', role='admin')
        self.project = make_project(self.owner, self.cat, title='Report me')

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse('reports_queue'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_regular_user_gets_403(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(reverse('reports_queue'))
        self.assertEqual(response.status_code, 403)

    def test_moderator_can_open_queue(self):
        self.client.login(username='mod', password='pass12345')
        response = self.client.get(reverse('reports_queue'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Report Triage')

    def test_admin_can_open_queue(self):
        self.client.login(username='adminuser', password='pass12345')
        response = self.client.get(reverse('reports_queue'))
        self.assertEqual(response.status_code, 200)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ReportCreationTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.reporter = make_user('reporter')
        self.mod = make_user('mod', role='moderator')
        self.admin = make_user('adminuser', role='admin')
        self.project = make_project(self.owner, self.cat, title='Spam attack')

    def test_signed_in_report_creates_one_row_and_notifies_staff(self):
        self.client.login(username='reporter', password='pass12345')
        response = self.client.post(
            f'/app/{self.project.slug}/report/',
            {'reason': 'spam', 'details': 'Repeated links to a dodgy site.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AppReport.objects.filter(project=self.project, reason='spam').exists())
        # Every moderator-bearing staff member gets an inbox row.
        for staff in (self.mod, self.admin):
            self.assertTrue(
                Notification.objects.filter(user=staff, kind='report').exists(),
                f'{staff.username} should be notified about the report.',
            )
        # A regular owner should not be notified about a report on themselves.
        self.assertFalse(Notification.objects.filter(user=self.owner, kind='report').exists())

    def test_duplicate_report_from_same_user_within_24h_is_deduped(self):
        self.client.login(username='reporter', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/report/', {'reason': 'spam', 'details': 'first'})
        response = self.client.post(
            f'/app/{self.project.slug}/report/',
            {'reason': 'malware', 'details': 'second'},
        )
        self.assertEqual(response.status_code, 302)
        rows = AppReport.objects.filter(project=self.project, user=self.reporter)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().reason, 'spam')

    def test_reporter_can_report_again_after_cooldown_expires(self):
        self.client.login(username='reporter', password='pass12345')
        first, _created = self._create_report(days_ago=2)
        self.assertEqual(first.status, 'open')
        second, created = self._create_report(days_ago=0)
        self.assertTrue(created)
        self.assertNotEqual(first.pk, second.pk)

    def _create_report(self, days_ago):
        from gallery.reports import create_report
        report, created = create_report(
            self.project, self.reporter, 'other', f'x-{days_ago}',
        )
        AppReport.objects.filter(pk=report.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago),
        )
        return AppReport.objects.get(pk=report.pk), created

    def test_anonymous_report_creates_row_without_user(self):
        response = self.client.post(
            f'/app/{self.project.slug}/report/',
            {'reason': 'copyright', 'details': 'This is my code.'},
        )
        self.assertEqual(response.status_code, 302)
        row = AppReport.objects.get(project=self.project, user__isnull=True)
        self.assertEqual(row.reason, 'copyright')

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ReportResolutionTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.reporter = make_user('reporter')
        self.mod = make_user('mod', role='moderator')
        self.admin = make_user('adminuser', role='admin')
        self.project = make_project(self.owner, self.cat, title='Reported vibe')

    def _post(self, report, user, decision, note='the note'):
        self.client.login(username=user.username, password='pass12345')
        return self.client.post(
            reverse('report_action', args=[report.pk]),
            {'decision': decision, 'note': note, 'next': reverse('reports_queue')},
        )

    def test_moderator_ignore_resolves_with_no_action_and_keeps_vibe(self):
        report = AppReport.objects.create(project=self.project, user=self.reporter, reason='other')
        response = self._post(report, self.mod, 'ignore', 'Looks fine.')
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, 'ignored')
        self.assertEqual(report.outcome, 'no_action')
        self.assertEqual(report.handled_by, self.mod)
        self.assertIsNotNone(report.handled_at)
        self.assertIn('Looks fine.', report.note)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'published')

    def test_moderator_quarantine_resolves_report_and_holds_vibe(self):
        report = AppReport.objects.create(project=self.project, user=self.reporter, reason='malware')
        response = self._post(report, self.mod, 'quarantine', 'Needs a closer look.')
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, 'resolved')
        self.assertEqual(report.outcome, 'quarantined')
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'quarantined')
        scan = ScanJob.objects.filter(project=self.project).first()
        self.assertIsNotNone(scan)
        self.assertEqual(scan.status, 'quarantined')

    def test_moderator_cannot_delete_vibe(self):
        report = AppReport.objects.create(project=self.project, user=self.reporter, reason='spam')
        response = self._post(report, self.mod, 'delete')
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, 'open')  # refused, still open
        self.assertTrue(AppProject.objects.filter(pk=self.project.pk).exists())

    def test_admin_delete_unpaid_vibe_hard_deletes_and_audits(self):
        report = AppReport.objects.create(project=self.project, user=self.reporter, reason='spam')
        response = self._post(report, self.admin, 'delete', 'Confirmed malware.')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AppProject.objects.filter(pk=self.project.pk).exists())
        # Cascade removed the report, but the audit row can prove the act.
        self.assertFalse(AppReport.objects.filter(pk=report.pk).exists())
        self.assertTrue(
            self.admin.admin_logs.filter(action='report_delete', target=self.project.slug).exists()
        )

    def test_admin_remove_paid_vibe_soft_deletes_and_preserves_buyer(self):
        self.project.star_cost = 3
        self.project.save(update_fields=['star_cost'])
        Trade.objects.create(buyer=self.reporter, seller=self.owner, project=self.project, cost=3)

        report = AppReport.objects.create(project=self.project, user=self.reporter, reason='copyright')
        sibling = AppReport.objects.create(project=self.project, user=self.reporter, reason='other')
        response = self._post(report, self.admin, 'remove')
        self.assertEqual(response.status_code, 302)

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'removed')
        self.assertTrue(Trade.objects.filter(project=self.project).exists())
        report.refresh_from_db()
        sibling.refresh_from_db()
        self.assertEqual(report.status, 'resolved')
        self.assertEqual(report.outcome, 'removed')
        # The sibling was auto-resolved; the queue can never re-flag a gone vibe.
        self.assertEqual(sibling.status, 'resolved')
        self.assertEqual(sibling.outcome, 'removed')

    def test_already_resolved_report_is_not_acted_on_twice(self):
        report = AppReport.objects.create(project=self.project, user=self.reporter, reason='other')
        self._post(report, self.mod, 'ignore', 'first')
        report.refresh_from_db()
        response = self._post(report, self.admin, 'delete')
        report.refresh_from_db()
        # First resolution stands; a second request cannot flip the outcome.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(report.status, 'ignored')
        self.assertEqual(report.outcome, 'no_action')
        self.assertEqual(report.handled_by, self.mod)

    def test_missing_or_unknown_decision_is_rejected(self):
        report = AppReport.objects.create(project=self.project, user=self.reporter, reason='other')
        response = self._post(report, self.mod, 'not-a-thing')
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, 'open')
