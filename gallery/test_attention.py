"""Tests for attention cases: duplicates, malfunctions, the countdown, the colours.

The suite is organised as the promises the feature makes, because each one is a
sentence a user reads somewhere in the product:

1. Detection — what becomes a case, and what must never become one.
2. Severity — every notification has a colour, and the inbox reads critical first.
3. Reminders — every 30 minutes, one row, never an inbox flood.
4. The 7-day decision — the system chooses, and writes down why.
5. The keeper strategy — receipts beat popularity, deterministically.
6. FINAL DELETE — the only erasure, and the 24 hours of silence that counts as yes.
7. Undo — a machine decision must be reversible.
8. Ownership — nobody else's case is reachable, and nothing destructive is a GET.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gallery import attention
from gallery.models import (
    AppProject, AttentionCandidate, AttentionCase, Notification, Sale, ScanJob, Trade,
)
from gallery.notify import CATEGORY_META, CATEGORY_OF_KIND, inbox_queryset, notify
from gallery.tests import make_category, make_project, make_user


def tree(*paths):
    """A file_tree in the shape ziputil.build_tree writes (None leaves)."""
    return {path: None for path in paths}


import itertools

_BUILD_SEQ = itertools.count(1)


def build(owner, category, **kwargs):
    """A healthy build, by default.

    AppUploadForm and QuickPublishForm both refuse a project with neither a ZIP
    nor an inline snippet, so a content-free test fixture would be a real
    'empty_shell' malfunction — every test would start with a case already open
    and none of them would be testing what they say. Tests that WANT the
    malfunction call make_project directly.
    """
    if 'html_code' not in kwargs:
        # Unique per call. Two builds that accidentally share their snippet would
        # score 45 points for "byte-identical code" and a test about something
        # else would be a test about duplicates.
        kwargs['html_code'] = (
            f'<main><h1>{kwargs.get("title", "build")}</h1>'
            f'<p>{"content " * 20}{next(_BUILD_SEQ)}</p></main>'
        )
    return make_project(owner, category, **kwargs)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class AttentionFactories(TestCase):
    """Shared setup + the builders every suite below uses."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.other = make_user('stranger')

    def make_duplicate_pair(self, owner=None, title='Invoice Dashboard',
                            files=('index.html', 'app.py', 'requirements.txt'),
                            status='published', **kwargs):
        """Two builds of the same owner that any person would call the same build."""
        owner = owner or self.owner
        first = build(owner, self.cat, title=title, status=status,
                      file_tree=tree(*files), file_count=len(files), **kwargs)
        second = build(owner, self.cat, title=title, status=status,
                       file_tree=tree(*files), file_count=len(files), **kwargs)
        return first, second

    def make_distinct_pair(self, owner=None):
        owner = owner or self.owner
        first = build(owner, self.cat, title='Invoice Dashboard',
                      file_tree=tree('index.html', 'app.py'), file_count=2)
        second = build(owner, self.cat, title='Weather Widget',
                      file_tree=tree('widget.js', 'styles.css', 'README.md'), file_count=3)
        return first, second


# ----------------------------------------------------------------------
# 1. Detection
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class DuplicateDetectionTests(AttentionFactories):

    def test_identical_uploads_open_one_case(self):
        first, second = self.make_duplicate_pair()
        created = attention.detect_for_user(self.owner)
        self.assertEqual(created['duplicates'], 1)
        case = AttentionCase.objects.get()
        self.assertEqual(case.kind, 'duplicate')
        self.assertEqual(case.user, self.owner)
        self.assertEqual(case.candidates.count(), 2)
        self.assertEqual(case.subject, second, 'the newer upload is the one that caused the collision')

    def test_both_published_is_critical_one_pending_is_only_action(self):
        # Two live copies of one build confuse visitors — that is the damaging
        # case. One still sitting in review is a workshop problem.
        self.make_duplicate_pair(status='published')
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.get().severity, 'critical')

        AttentionCase.objects.all().delete()
        AppProject.objects.all().delete()
        first, second = self.make_duplicate_pair(title='Other Thing')
        second.status = 'pending'
        second.save(update_fields=['status'])
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.get().severity, 'action')

    def test_different_builds_never_open_a_case(self):
        self.make_distinct_pair()
        self.assertEqual(attention.detect_for_user(self.owner)['duplicates'], 0)
        self.assertEqual(AttentionCase.objects.count(), 0)

    def test_a_remix_is_credit_not_duplication(self):
        # The README is explicit: lineage is credit. Flagging a fork as a
        # duplicate of its parent would teach builders that remixing gets their
        # work deleted.
        parent, child = self.make_duplicate_pair(title='Remixable')
        child.forked_from = parent
        child.save(update_fields=['forked_from'])
        self.assertEqual(attention.detect_for_user(self.owner)['duplicates'], 0)

    def test_two_remixes_of_the_same_parent_are_not_duplicates_of_each_other(self):
        parent = build(self.owner, self.cat, title='Parent',
                       file_tree=tree('index.html', 'app.py'), file_count=2)
        a = build(self.owner, self.cat, title='Parent', forked_from=parent,
                         file_tree=tree('index.html', 'app.py'), file_count=2)
        b = build(self.owner, self.cat, title='Parent', forked_from=parent,
                         file_tree=tree('index.html', 'app.py'), file_count=2)
        self.assertEqual(attention.detect_for_user(self.owner)['duplicates'], 0)

    def test_another_owners_similar_build_is_not_your_case(self):
        # Cross-user similarity is a plagiarism question for moderators, not a
        # timer that deletes a stranger's work.
        mine = build(self.owner, self.cat, title='Invoice Dashboard',
                            file_tree=tree('index.html', 'app.py'), file_count=2)
        theirs = build(self.other, self.cat, title='Invoice Dashboard',
                       file_tree=tree('index.html', 'app.py'), file_count=2)
        attention.detect_for_user(self.owner)
        attention.detect_for_user(self.other)
        self.assertEqual(AttentionCase.objects.count(), 0)

    def test_detection_is_idempotent(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        first = AttentionCase.objects.get()
        for _ in range(3):
            attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.count(), 1)
        self.assertEqual(AttentionCase.objects.get().pk, first.pk)
        self.assertEqual(Notification.objects.filter(attention_case=first).count(), 1)

    def test_a_third_copy_joins_the_open_case(self):
        first, second = self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        third = build(self.owner, self.cat, title='Invoice Dashboard',
                             file_tree=tree('index.html', 'app.py', 'requirements.txt'), file_count=3)
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.count(), 1, 'one decision per problem')
        case.refresh_from_db()
        self.assertEqual(case.candidates.count(), 3)

    def test_a_parked_copy_is_not_half_of_a_new_duplicate(self):
        first, second = self.make_duplicate_pair()
        second.status = 'removed'
        second.save(update_fields=['status'])
        self.assertEqual(attention.detect_for_user(self.owner)['duplicates'], 0)

    def test_identical_inline_code_with_the_same_title_is_a_duplicate(self):
        code = '<div class="dashboard">' + ('<p>row</p>' * 12) + '</div>'
        build(self.owner, self.cat, title='Snippet Dash', html_code=code)
        build(self.owner, self.cat, title='Snippet Dash', html_code=code)
        self.assertEqual(attention.detect_for_user(self.owner)['duplicates'], 1)

    def test_a_shared_single_file_alone_is_not_enough(self):
        # 'index.html' is everybody's first file. One shared path plus a shared
        # title must not reach the threshold, or every static snippet on the
        # platform becomes a case.
        build(self.owner, self.cat, title='Landing', file_tree=tree('index.html'), file_count=1)
        build(self.owner, self.cat, title='Landing', file_tree=tree('index.html'), file_count=1)
        self.assertEqual(attention.detect_for_user(self.owner)['duplicates'], 0)

    def test_weak_signals_alone_cannot_reach_the_threshold(self):
        # The arithmetic the O(n) bucketing in _pairs_worth_comparing relies on.
        # If a weak signal is ever raised past this, bucketing starts missing
        # duplicates — so the constant has to fail loudly here instead.
        self.assertLess(attention.WEAK_SIGNAL_MAX, attention.duplicate_threshold())
        self.assertEqual(attention.WEAK_SIGNAL_MAX,
                         attention.SIGNAL_POINTS['same_title']
                         + attention.SIGNAL_POINTS['same_slug']
                         + attention.SIGNAL_POINTS['same_stack'])

    def test_bucketing_agrees_with_comparing_every_pair(self):
        # Same answer, cheaper path: the bucketed generator must not drop a pair
        # that a full comparison would have opened a case for.
        a, b = self.make_duplicate_pair()
        c = build(self.owner, self.cat, title='Weather Widget',
                         file_tree=tree('widget.js'), file_count=1)
        prints = [(p, attention.fingerprint(p)) for p in (a, b, c)]
        bucketed = {(x.pk, y.pk) for x, y, _, _ in attention._pairs_worth_comparing(prints, 70)}
        self.assertIn((a.pk, b.pk), bucketed)
        full = {(x.pk, y.pk) for x, y, _, _ in attention._pairs_worth_comparing(prints, 1)}
        self.assertEqual(len(full), 3)
        for pair in bucketed:
            self.assertIn(pair, full)

    def test_detection_can_be_switched_off(self):
        self.make_duplicate_pair()
        with self.settings(ATTENTION_ENABLED=False):
            self.assertEqual(attention.detect_for_user(self.owner)['duplicates'], 0)
        self.assertEqual(AttentionCase.objects.count(), 0)


# ----------------------------------------------------------------------
# 1b. Malfunction detection
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class MalfunctionDetectionTests(AttentionFactories):

    def test_a_quarantined_build_is_a_critical_case(self):
        project = build(self.owner, self.cat, title='Leaky', status='quarantined')
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        self.assertEqual(case.kind, 'malfunction')
        self.assertEqual(case.severity, 'critical')
        self.assertEqual(case.evidence['code'], 'quarantined')
        self.assertIn('secret', case.detail.lower() + case.headline.lower())
        self.assertTrue(case.fix_hint)

    def test_a_failed_scan_is_reported_as_our_failure(self):
        project = build(self.owner, self.cat, title='Unscanned', status='pending')
        ScanJob.objects.create(project=project, status='failed')
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.get().evidence['code'], 'scan_failed')

    def test_a_preview_pointing_at_a_missing_file_is_a_case(self):
        project = build(self.owner, self.cat, title='Static Site',
                               preview_mode='static_zip', static_entry='public/index.html',
                               file_tree=tree('index.html', 'styles.css'), file_count=2)
        project.zip_file = 'apps/zips/fake.zip'
        project.save(update_fields=['zip_file'])
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        self.assertEqual(case.evidence['code'], 'broken_preview')
        self.assertEqual(case.severity, 'action')
        self.assertIn('public/index.html', case.detail)

    def test_a_build_stuck_in_the_queue_becomes_a_case_after_the_window(self):
        project = build(self.owner, self.cat, title='Slow Lane', status='pending')
        ScanJob.objects.create(project=project, status='queued')
        self.assertEqual(attention.detect_for_user(self.owner)['malfunctions'], 0,
                         'a fresh upload in the queue is normal, not broken')
        AppProject.objects.filter(pk=project.pk).update(created_at=timezone.now() - timedelta(hours=49))
        project.refresh_from_db()
        self.assertEqual(attention.detect_for_user(self.owner)['malfunctions'], 1)
        self.assertEqual(AttentionCase.objects.get().evidence['code'], 'stuck_pending')

    def test_a_published_build_with_nothing_to_run_is_a_case(self):
        # No ZIP and no snippet: the publish form refuses this, so it can only
        # exist through a data problem — which is exactly what a malfunction
        # case is for.
        make_project(self.owner, self.cat, title='Empty Shell', status='published')
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.get().evidence['code'], 'empty_shell')

    def test_a_healthy_build_opens_nothing(self):
        build(self.owner, self.cat, title='Healthy', status='published',
                     html_code='<h1>Works</h1>' * 10)
        self.assertEqual(attention.detect_for_user(self.owner)['malfunctions'], 0)

    def test_one_case_per_problem_even_when_two_are_true(self):
        # Quarantined AND an empty shell: the owner gets the most damaging true
        # statement, once — not two cards about one build.
        build(self.owner, self.cat, title='Broken', status='quarantined')
        attention.detect_for_user(self.owner)
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.count(), 1)
        self.assertEqual(AttentionCase.objects.get().evidence['code'], 'quarantined')

    def test_a_dismissed_malfunction_is_not_reopened(self):
        build(self.owner, self.cat, title='Broken', status='quarantined')
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.dismiss(case, actor=self.owner, reason='will_fix')
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.count(), 1)
        self.assertEqual(AttentionCase.objects.get().status, 'dismissed')


# ----------------------------------------------------------------------
# 2. Severity categories and the coloured inbox
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class NotificationCategoryTests(AttentionFactories):

    def test_every_kind_has_a_category(self):
        kinds = [key for key, _label in Notification.KIND_CHOICES]
        missing = [kind for kind in kinds if kind not in CATEGORY_OF_KIND]
        self.assertEqual(missing, [], f'unmapped kinds fall back to grey/system: {missing}')

    def test_every_category_has_a_colour_a_rank_and_a_word(self):
        for key, meta in CATEGORY_META.items():
            self.assertTrue(meta['swatch'].startswith('#'))
            self.assertTrue(meta['label'])
            self.assertTrue(meta['meaning'])
            self.assertIsInstance(meta['rank'], int)
        ranks = [meta['rank'] for meta in CATEGORY_META.values()]
        self.assertEqual(len(set(ranks)), len(ranks), 'ranks must not collide or the sort is arbitrary')
        self.assertLess(CATEGORY_META['critical']['rank'], CATEGORY_META['social']['rank'])

    def test_the_migration_map_matches_the_live_map(self):
        # The backfill copies the map inline, because a migration must describe
        # the past with the code as it was — importing live code would let a
        # later edit silently rewrite history on the next `migrate`. This is the
        # pin that keeps the copy honest. (import_module, not import: a module
        # name starting with a digit is not a valid identifier.)
        import importlib
        module = importlib.import_module('gallery.migrations.0046_backfill_notification_category')
        self.assertEqual(module.CATEGORY_OF_KIND, CATEGORY_OF_KIND)

    def test_notify_stores_the_category_for_the_kind(self):
        note = notify(self.owner, 'quarantined', 'Blocked', 'why', '/x/')
        self.assertEqual(note.category, 'critical')
        note = notify(self.owner, 'star', 'Somebody starred', '', '/y/')
        self.assertEqual(note.category, 'social')

    def test_a_caller_can_override_the_severity(self):
        note = notify(self.owner, 'malfunction', 'Broken', '', '/z/', category='critical')
        self.assertEqual(note.category, 'critical')

    def test_the_inbox_sorts_critical_above_a_newer_social_row(self):
        old_critical = notify(self.owner, 'quarantined', 'Old and blocking', '', '/a/')
        Notification.objects.filter(pk=old_critical.pk).update(created_at=timezone.now() - timedelta(days=2))
        notify(self.owner, 'star', 'Fresh star', '', '/b/')
        notes = list(inbox_queryset(self.owner))
        self.assertEqual(notes[0].category, 'critical')
        self.assertEqual(notes[-1].category, 'social')

    def test_the_inbox_page_draws_the_stripe_and_the_word(self):
        notify(self.owner, 'quarantined', 'Blocked build', 'Fix it', '/a/')
        notify(self.owner, 'star', 'Somebody starred', '', '/b/')
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(reverse('notifications'))
        self.assertContains(response, 'bv-notif--critical')
        self.assertContains(response, 'bv-notif--social')
        # The colour is never the only channel: the category WORD renders too.
        self.assertContains(response, 'Critical')
        self.assertContains(response, 'Social')
        self.assertContains(response, 'bv-legend')

    def test_the_inbox_can_be_filtered_to_one_severity(self):
        notify(self.owner, 'quarantined', 'Blocked build', 'Fix it', '/a/')
        notify(self.owner, 'star', 'Somebody starred', '', '/b/')
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(reverse('notifications') + '?category=critical')
        self.assertContains(response, 'Blocked build')
        self.assertNotContains(response, 'Somebody starred')

    def test_an_attention_case_strips_its_own_severity(self):
        self.make_duplicate_pair(status='published')
        attention.detect_for_user(self.owner)
        note = Notification.objects.get()
        self.assertEqual(note.kind, 'duplicate')
        self.assertEqual(note.category, 'critical')
        self.assertEqual(note.attention_case, AttentionCase.objects.get())
        self.assertEqual(note.url, reverse('attention_case', args=[note.attention_case_id]))

    def test_mark_all_read_cannot_silence_a_countdown(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        note = Notification.objects.get()
        self.assertTrue(note.is_read is False)
        self.client.login(username='owner', password='pass12345')
        # Opening the inbox marks ordinary rows read…
        self.client.get(reverse('notifications'))
        note.refresh_from_db()
        self.assertFalse(note.is_read, 'a case still waiting on an answer stays unread')
        # …and "mark all read" must not be a way to silence it either.
        response = self.client.post(reverse('notifications_mark_all_read'))
        self.assertEqual(response.json()['held'], 1)
        note.refresh_from_db()
        self.assertFalse(note.is_read)

    def test_a_closed_case_can_be_marked_read(self):
        # A decision still waiting on FINAL DELETE stays unread on purpose; a
        # case the owner has finished with does not.
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.dismiss(case, actor=self.owner, reason='not_a_duplicate')
        self.client.login(username='owner', password='pass12345')
        self.client.post(reverse('notifications_mark_all_read'))
        note = Notification.objects.filter(attention_case=case).first()
        self.assertTrue(note.is_read)


# ----------------------------------------------------------------------
# 3. Reminders
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class ReminderTests(AttentionFactories):

    def open_case(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        return AttentionCase.objects.get()

    def test_no_reminder_before_the_interval(self):
        case = self.open_case()
        self.assertEqual(attention.remind_due(), 0)
        case.refresh_from_db()
        self.assertEqual(case.remind_count, 0)

    def test_a_reminder_lands_after_thirty_minutes(self):
        case = self.open_case()
        AttentionCase.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(minutes=31))
        self.assertEqual(attention.remind_due(), 1)
        case.refresh_from_db()
        self.assertEqual(case.remind_count, 1)
        self.assertIsNotNone(case.reminded_at)

    def test_the_cadence_is_the_setting_the_copy_quotes(self):
        case = self.open_case()
        AttentionCase.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(minutes=29))
        self.assertEqual(attention.remind_due(), 0, 'one minute early is not the promise we printed')
        AttentionCase.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(minutes=30), reminded_at=None)
        self.assertEqual(attention.remind_due(), 1)

    def test_a_reminder_bumps_one_row_instead_of_adding_one(self):
        case = self.open_case()
        for i in range(1, 6):
            AttentionCase.objects.filter(pk=case.pk).update(
                reminded_at=timezone.now() - timedelta(minutes=31))
            attention.remind_due()
        case.refresh_from_db()
        self.assertEqual(case.remind_count, 5)
        self.assertEqual(Notification.objects.filter(attention_case=case).count(), 1,
                         '336 nudges over 7 days must still be one inbox row')
        note = Notification.objects.get()
        self.assertFalse(note.is_read, 'a reminder makes the decision unread again')
        self.assertIsNotNone(note.reminded_at)
        self.assertIn('reminder 5', note.body.lower())

    def test_reminders_stop_once_the_owner_has_seen_the_decision(self):
        case = self.open_case()
        attention.decide(case, keeper_project=case.candidates.first().project,
                         actor=self.owner, source='system')
        case.refresh_from_db()
        AttentionCase.objects.filter(pk=case.pk).update(
            acknowledged_at=None, reminded_at=None,
            created_at=timezone.now() - timedelta(minutes=31))
        self.assertEqual(attention.remind_due(), 1, 'unseen decisions keep nagging')
        attention.acknowledge(case)
        AttentionCase.objects.filter(pk=case.pk).update(
            reminded_at=timezone.now() - timedelta(minutes=31))
        self.assertEqual(attention.remind_due(), 0, 'a deadline they are already watching needs no bump')

    def test_answered_and_dismissed_cases_are_not_reminded(self):
        case = self.open_case()
        attention.dismiss(case, actor=self.owner, reason='not_a_duplicate')
        AttentionCase.objects.filter(pk=case.pk).update(
            reminded_at=timezone.now() - timedelta(hours=3))
        self.assertEqual(attention.remind_due(), 0)

    def test_the_reminder_countdown_is_in_the_copy(self):
        case = self.open_case()
        title, body = attention.notification_copy(case)
        self.assertIn('7 days left to decide', body)
        self.assertIn('pick which one to keep', title.lower())


# ----------------------------------------------------------------------
# 4. The 7-day decision
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class ExpiryTests(AttentionFactories):

    def test_an_open_case_is_not_decided_early(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        self.assertEqual(attention.expire_due(), 0)
        self.assertEqual(AttentionCase.objects.get().status, 'open')

    def test_the_deadline_is_the_number_the_copy_quotes(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        expected = case.created_at + timedelta(days=attention.decision_days())
        self.assertLess(abs((case.expires_at - expected).total_seconds()), 5)
        self.assertEqual(attention.decision_days(), 7)

    def test_after_seven_days_the_system_decides_and_explains(self):
        first, second = self.make_duplicate_pair()
        second.stars = 4
        second.save(update_fields=['stars'])
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        AttentionCase.objects.filter(pk=case.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(attention.expire_due(), 1)

        case.refresh_from_db()
        self.assertEqual(case.status, 'decided')
        self.assertEqual(case.decision_source, 'system')
        self.assertIsNotNone(case.decided_at)
        self.assertIsNotNone(case.final_delete_at)
        self.assertIsNone(case.acknowledged_at, 'the owner has not seen it yet — the clock waits for that')
        # The strategic explanation: what was kept, why, the rule, and the promise
        # that nothing is erased yet.
        self.assertIn('BlaqVibes kept', case.rationale)
        self.assertIn('Why this one:', case.rationale)
        self.assertIn('The rule, in order:', case.rationale)
        self.assertIn('Nothing is erased yet', case.rationale)
        self.assertIn('FINAL DELETE', case.rationale)
        self.assertTrue(case.scores, 'the arithmetic behind the why is stored, not just the prose')

    def test_the_owner_is_told_the_decision_landed(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        AttentionCase.objects.filter(pk=case.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        attention.expire_due()
        note = Notification.objects.filter(attention_case=case).get()
        self.assertIn('BlaqVibes kept', note.title)
        self.assertFalse(note.is_read)

    def test_the_loser_is_parked_not_erased(self):
        first, second = self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.decide(case, keeper_project=first, actor=None, source='system')
        second.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(second.status, 'removed', 'off the public site')
        self.assertEqual(first.status, 'published', 'the keeper is untouched')
        self.assertTrue(AppProject.objects.filter(pk=second.pk).exists(), 'parked, never destroyed')

    def test_expiry_is_idempotent(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        AttentionCase.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        attention.expire_due()
        self.assertEqual(attention.expire_due(), 0)
        self.assertEqual(AttentionCase.objects.filter(status='decided').count(), 1)

    def test_the_sweep_runs_every_stage_in_a_safe_order(self):
        self.make_duplicate_pair()
        result = attention.sweep()
        self.assertEqual(result['detected']['duplicates'], 1)
        case = AttentionCase.objects.get()
        AttentionCase.objects.filter(pk=case.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1),
            reminded_at=timezone.now() - timedelta(hours=1))
        result = attention.sweep()
        self.assertEqual(result['expired'], 1)
        self.assertEqual(result['erased'], 0, 'a decision made in this pass cannot also be erased by it')


# ----------------------------------------------------------------------
# 5. The keeper strategy
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class KeeperStrategyTests(AttentionFactories):

    def test_a_receipt_outweighs_everything_else(self):
        # The one rule that is not a weight but a constraint: a purchase is a
        # promise, so the copy somebody paid for can never be the one that goes.
        first, second = self.make_duplicate_pair()
        first.status = 'pending'
        first.trust = 'unknown'
        first.save()
        second.stars = 5
        second.review_count = 5
        second.views = 5000
        second.trust = 'verified'
        second.save()
        Trade.objects.create(buyer=self.other, seller=self.owner, project=first, cost=3)
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, first)
        self.assertIn('receipt', case.rationale.lower())

    def test_the_receipt_sentence_agrees_with_itself(self):
        # "1 paid sale exist for it" is exactly the small sloppiness that makes
        # a machine's explanation read like a machine — and this feature's
        # whole claim is that the reasoning is worth trusting. One receipt is a
        # singular subject; two kinds of receipt together are not.
        project = build(self.owner, self.cat, title='Receipt Wording')
        Sale.objects.create(buyer=self.other, seller=self.owner, project=project, amount_zar=90)
        _score, reasons = attention.score_keeper(project, all_projects=[project])
        self.assertTrue(any('1 paid sale exists for it' in r for r in reasons), reasons)

        Sale.objects.create(buyer=make_user('buyer_two'), seller=self.owner,
                            project=project, amount_zar=40)
        Trade.objects.create(buyer=self.other, seller=self.owner, project=project, cost=2)
        _score, reasons = attention.score_keeper(project, all_projects=[project])
        self.assertTrue(any('exist for it' in r and '2 paid sales' in r for r in reasons), reasons)
        self.assertTrue(any('1 star trade' in r for r in reasons), reasons)

    def test_a_review_tally_with_no_average_does_not_quote_zero_stars(self):
        # review_count is a cached tally and avg_rating only refreshes when a
        # review actually lands, so the two can disagree. Printing "9 reviews
        # (0.0★ average)" contradicts itself on the page where we are asking
        # somebody to trust our arithmetic — quote the average only when there
        # is one.
        project = build(self.owner, self.cat, title='Tally Only')
        project.review_count = 9
        project.avg_rating = 0
        project.save(update_fields=['review_count', 'avg_rating'])
        _score, reasons = attention.score_keeper(project, all_projects=[project])
        self.assertTrue(any('9 reviews.' in r for r in reasons), reasons)
        self.assertFalse(any('★' in r for r in reasons), reasons)

        project.avg_rating = 4.5
        project.save(update_fields=['avg_rating'])
        _score, reasons = attention.score_keeper(project, all_projects=[project])
        self.assertTrue(any('9 reviews (4.5★ average).' in r for r in reasons), reasons)

    def test_published_beats_pending(self):
        first, second = self.make_duplicate_pair()
        first.status = 'pending'
        first.save(update_fields=['status'])
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, second)

    def test_a_verified_scan_beats_an_unscanned_copy(self):
        first, second = self.make_duplicate_pair(status='published')
        first.trust = 'verified'
        second.trust = 'unknown'
        AppProject.objects.filter(pk=first.pk).update(trust='verified')
        AppProject.objects.filter(pk=second.pk).update(trust='unknown')
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, first)

    def test_proof_you_wrote_outranks_popularity(self):
        # The product standard: capability must be believable. Stars are capped
        # so a loud copy cannot beat a documented one.
        first, second = self.make_duplicate_pair()
        AppProject.objects.filter(pk=first.pk).update(
            problem_statement='Invoices chase themselves.', human_did='Spec, tests, schema.',
            ai_got_wrong='It hallucinated the tax rules.', stars=0, views=0)
        AppProject.objects.filter(pk=second.pk).update(stars=5, views=5000, review_count=5, avg_rating=5)
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, first)
        self.assertIn('proof', case.rationale.lower())

    def test_the_copy_you_were_still_working_on_wins_a_tie(self):
        first, second = self.make_duplicate_pair()
        AppProject.objects.filter(pk=second.pk).update(updated_at=timezone.now())
        AppProject.objects.filter(pk=first.pk).update(updated_at=timezone.now() - timedelta(days=200))
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, second)

    def test_only_the_fresher_copy_is_called_the_working_copy(self):
        # Both halves of a duplicate were usually touched this month. Paying
        # both of them for recency tells the owner that each one is "the" copy
        # they were still working on — the same sentence twice, deciding
        # nothing — so recency has to be a comparison between the peers.
        first, second = self.make_duplicate_pair()
        AppProject.objects.filter(pk=first.pk).update(updated_at=timezone.now() - timedelta(days=2))
        AppProject.objects.filter(pk=second.pk).update(updated_at=timezone.now() - timedelta(hours=2))
        first.refresh_from_db()
        second.refresh_from_db()
        peers = [first, second]
        score_first, reasons_first = attention.score_keeper(first, all_projects=peers)
        score_second, reasons_second = attention.score_keeper(second, all_projects=peers)
        self.assertTrue(any('still working on' in r for r in reasons_second))
        self.assertFalse(any('still working on' in r for r in reasons_first),
                         'the stale copy must not also claim to be the one in progress')
        self.assertGreater(score_second, score_first)
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, second)

    def test_a_lone_broken_build_is_still_judged_on_the_calendar(self):
        # malfunction_verdict scores ONE project with no peers to compare
        # against, so the 30-day window has to keep working on its own.
        project = build(self.owner, self.cat, title='Solo Broken', status='quarantined')
        AppProject.objects.filter(pk=project.pk).update(updated_at=timezone.now() - timedelta(days=2))
        project.refresh_from_db()
        _score, reasons = attention.score_keeper(project, all_projects=[project])
        self.assertTrue(any('still working on' in r for r in reasons))
        stale = build(self.owner, self.cat, title='Solo Stale', status='quarantined')
        AppProject.objects.filter(pk=stale.pk).update(updated_at=timezone.now() - timedelta(days=200))
        stale.refresh_from_db()
        _score, reasons = attention.score_keeper(stale, all_projects=[stale])
        self.assertFalse(any('still working on' in r for r in reasons))

    def test_the_same_facts_always_produce_the_same_keeper(self):
        first, second = self.make_duplicate_pair()
        first.stars = 2
        first.save(update_fields=['stars'])
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        rankings = []
        for _ in range(3):
            rankings.append([row['project'].pk for row in attention.rank_candidates(case)])
        self.assertEqual(rankings[0], rankings[1], 'a non-deterministic "why" is fiction')
        self.assertEqual(rankings[1], rankings[2])

    def test_remix_children_protect_a_copy_from_being_dropped(self):
        first, second = self.make_duplicate_pair()
        for i in range(2):
            build(self.other, self.cat, title=f'Remix {i}', forked_from=first)
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, first)
        self.assertIn('remixed', case.rationale.lower())

    def test_the_rationale_names_the_uploads_so_old_and_new_are_tellable(self):
        # The owner asked to see WHEN each copy arrived — that is how they tell
        # which one is which.
        first, second = self.make_duplicate_pair()
        AppProject.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=200))
        first.refresh_from_db()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertIn('uploaded', case.rationale.lower())
        for candidate in case.candidates.all():
            self.assertTrue(candidate.snapshot['uploaded_on'])
            self.assertTrue(candidate.snapshot['status_label'])

    def test_a_broken_build_nobody_depends_on_is_dropped(self):
        project = build(self.owner, self.cat, title='Broken', status='quarantined')
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.status, 'decided')
        project.refresh_from_db()
        self.assertEqual(project.status, 'removed')
        self.assertIn('re-upload', case.rationale.lower())

    def test_a_broken_build_somebody_paid_for_is_kept_for_repair(self):
        project = build(self.owner, self.cat, title='Broken But Sold', status='quarantined')
        Trade.objects.create(buyer=self.other, seller=self.owner, project=project, cost=3)
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.delegate(case)
        case.refresh_from_db()
        self.assertEqual(case.keeper, project)
        project.refresh_from_db()
        self.assertEqual(project.status, 'quarantined', 'kept for repair, not parked')
        self.assertIn('paid', case.rationale.lower())
        self.assertIsNone(case.final_delete_at, 'nothing parked means no erase clock')


# ----------------------------------------------------------------------
# 6. FINAL DELETE and the 24 hours of silence
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class FinalDeleteTests(AttentionFactories):

    def decided_case(self, source='user'):
        first, second = self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.decide(case, keeper_project=first, actor=self.owner, source=source)
        case.refresh_from_db()   # refresh_from_db() returns None — mutate, then return
        return case, first, second

    def test_a_user_decision_starts_the_24_hour_clock_immediately(self):
        case, first, second = self.decided_case(source='user')
        self.assertIsNotNone(case.acknowledged_at, 'clicking IS seeing it')
        expected = case.decided_at + timedelta(hours=attention.final_delete_hours())
        self.assertLess(abs((case.final_delete_at - expected).total_seconds()), 5)

    def test_the_inbox_row_keeps_the_button_label_intact(self):
        # The countdown is glued into the notification body, and
        # str.capitalize() lowercases everything AFTER the first character —
        # which turned the deadline into "23h left to press final delete": an
        # inbox row naming a button that does not exist on the page it links
        # to. Sentence case is a one-character operation; it must not rewrite
        # labels on the way past.
        case, first, second = self.decided_case(source='user')
        AttentionCase.objects.filter(pk=case.pk).update(
            final_delete_at=timezone.now() + timedelta(hours=23, minutes=30))
        case.refresh_from_db()
        _title, body = attention.notification_copy(case)
        self.assertIn('23h left to press FINAL DELETE', body)
        self.assertNotIn('final delete', body)

    def test_opening_a_system_decision_starts_the_clock(self):
        case, first, second = self.decided_case(source='system')
        self.assertIsNone(case.acknowledged_at)
        backstop = case.final_delete_at
        attention.acknowledge(case)
        case.refresh_from_db()
        self.assertIsNotNone(case.acknowledged_at)
        self.assertLess(case.final_delete_at, backstop, 'seeing it shortens the grace, never lengthens it')
        expected = case.acknowledged_at + timedelta(hours=attention.final_delete_hours())
        self.assertLess(abs((case.final_delete_at - expected).total_seconds()), 5)

    def test_acknowledging_twice_never_pushes_the_deadline_out(self):
        case, first, second = self.decided_case(source='system')
        attention.acknowledge(case)
        case.refresh_from_db()
        first_deadline = case.final_delete_at
        attention.acknowledge(case)
        case.refresh_from_db()
        self.assertLessEqual(case.final_delete_at, first_deadline)

    def test_silence_after_the_clock_erases_the_parked_copy(self):
        case, first, second = self.decided_case(source='user')
        AttentionCase.objects.filter(pk=case.pk).update(final_delete_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(attention.delete_due(), 1)
        self.assertFalse(AppProject.objects.filter(pk=second.pk).exists(), 'hard delete, nothing was ever paid')
        case.refresh_from_db()
        self.assertEqual(case.status, 'deleted')
        candidate = case.candidates.get(project=None)
        self.assertEqual(candidate.outcome, 'deleted')
        self.assertEqual(candidate.snapshot['title'], second.title, 'the record survives the erasure')

    def test_nothing_is_erased_before_the_clock_runs_out(self):
        case, first, second = self.decided_case()
        self.assertEqual(attention.delete_due(), 0)
        self.assertTrue(AppProject.objects.filter(pk=second.pk).exists())

    def test_a_paid_copy_is_never_erased_and_the_owner_is_told_why(self):
        first, second = self.make_duplicate_pair()
        Trade.objects.create(buyer=self.other, seller=self.owner, project=second, cost=3)
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.decide(case, keeper_project=first, actor=self.owner, source='user')
        candidate = case.candidates.get(project=second)
        result = attention.final_delete(case, candidate)
        self.assertTrue(result['ok'])
        self.assertIn('cannot be erased', result['message'])
        self.assertTrue(AppProject.objects.filter(pk=second.pk).exists())
        second.refresh_from_db()
        self.assertEqual(second.status, 'removed')
        candidate.refresh_from_db()
        self.assertEqual(candidate.snapshot.get('cannot_erase'), 'paid')

    def test_the_owner_can_press_final_delete_themselves(self):
        case, first, second = self.decided_case()
        candidate = case.candidates.get(project=second)
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(reverse('attention_delete', args=[case.pk, candidate.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AppProject.objects.filter(pk=second.pk).exists())

    def test_final_delete_is_post_only(self):
        case, first, second = self.decided_case()
        candidate = case.candidates.get(project=second)
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(reverse('attention_delete', args=[case.pk, candidate.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(AppProject.objects.filter(pk=second.pk).exists())


# ----------------------------------------------------------------------
# 7. Undo
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class UndoTests(AttentionFactories):

    def test_undo_puts_the_parked_copy_back_at_its_old_status(self):
        first, second = self.make_duplicate_pair()
        AppProject.objects.filter(pk=second.pk).update(status='pending')
        second.refresh_from_db()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.decide(case, keeper_project=first, actor=self.owner, source='user')
        candidate = case.candidates.get(project=second)
        result = attention.final_delete  # noqa: F841 (kept explicit: undo, not delete)
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(reverse('attention_undo', args=[case.pk, candidate.pk]))
        self.assertEqual(response.status_code, 302)
        second.refresh_from_db()
        self.assertEqual(second.status, 'pending', 'restored to what it was, not silently published')
        case.refresh_from_db()
        self.assertEqual(case.status, 'restored')
        self.assertIsNone(case.final_delete_at)

    def test_let_me_choose_reopens_the_case_and_restores_everything(self):
        first, second = self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.decide(case, keeper_project=first, actor=None, source='system')
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(reverse('attention_reopen', args=[case.pk]))
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        second.refresh_from_db()
        first.refresh_from_db()
        self.assertEqual(case.status, 'open')
        self.assertEqual(case.decision_source, '')
        self.assertIsNone(case.keeper)
        self.assertIsNone(case.final_delete_at)
        self.assertEqual(second.status, 'published')
        self.assertEqual(first.status, 'published')
        self.assertGreater(case.expires_at, timezone.now(), 'a fresh full window to choose in')

    def test_an_erased_copy_cannot_be_undone(self):
        first, second = self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        attention.decide(case, keeper_project=first, actor=self.owner, source='user')
        candidate = case.candidates.get(project=second)
        attention.final_delete(case, candidate)
        candidate.refresh_from_db()
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(reverse('attention_undo', args=[case.pk, candidate.pk]), follow=True)
        self.assertContains(response, 'already gone')


# ----------------------------------------------------------------------
# 8. Ownership and the decision endpoints
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class AttentionViewTests(AttentionFactories):

    def login_owner(self):
        self.client.login(username='owner', password='pass12345')

    def open_case(self):
        first, second = self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        return AttentionCase.objects.get(), first, second

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse('attention_center'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_the_centre_lists_the_open_case_with_the_evidence(self):
        case, first, second = self.open_case()
        self.login_owner()
        response = self.client.get(reverse('attention_center'))
        self.assertContains(response, 'Needs your decision')
        self.assertContains(response, case.headline)
        # The owner asked to see which copy is old and which is new.
        self.assertContains(response, 'Uploaded')
        self.assertContains(response, 'Last updated')
        self.assertContains(response, 'Let BlaqVibes decide')
        self.assertContains(response, 'FINAL DELETE')
        self.assertContains(response, 'attention-case--critical')

    def test_the_centre_runs_detection_before_claiming_nothing_is_waiting(self):
        self.make_duplicate_pair()
        self.login_owner()
        self.assertEqual(AttentionCase.objects.count(), 0)
        response = self.client.get(reverse('attention_center'))
        self.assertEqual(AttentionCase.objects.count(), 1, 'the page must never lie about an empty queue')
        self.assertContains(response, AttentionCase.objects.get().headline)

    def test_the_owner_can_keep_the_copy_they_pick(self):
        case, first, second = self.open_case()
        self.login_owner()
        response = self.client.post(reverse('attention_decide', args=[case.pk]), {'project_id': second.pk})
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(case.status, 'decided')
        self.assertEqual(case.decision_source, 'user')
        self.assertEqual(case.keeper, second, 'the owner overrules the ranking')
        self.assertEqual(first.status, 'removed')
        self.assertEqual(second.status, 'published')
        self.assertIn('You kept', case.rationale)

    def test_picking_nothing_is_refused_politely(self):
        case, first, second = self.open_case()
        self.login_owner()
        response = self.client.post(reverse('attention_decide', args=[case.pk]), {}, follow=True)
        self.assertContains(response, 'Pick which copy to keep')
        case.refresh_from_db()
        self.assertEqual(case.status, 'open')

    def test_picking_a_build_outside_the_case_is_refused(self):
        case, first, second = self.open_case()
        outsider = build(self.owner, self.cat, title='Unrelated')
        self.login_owner()
        response = self.client.post(reverse('attention_decide', args=[case.pk]), {'project_id': outsider.pk})
        self.assertEqual(response.status_code, 404)
        case.refresh_from_db()
        self.assertEqual(case.status, 'open')

    def test_delegating_produces_the_same_answer_as_the_clock(self):
        # Two identical pairs, settled by two different paths: the owner's
        # "Let BlaqVibes decide" click, and the 7-day deadline the sweep
        # enforces while they are away. If those can disagree, the button and
        # the clock are two different products — so both must name the same
        # copy, and must be able to say why.
        case, first, second = self.open_case()
        clock_first, clock_second = self.make_duplicate_pair(title='Ledger Export')
        attention.detect_for_user(self.owner)
        clock_case = AttentionCase.objects.exclude(pk=case.pk).get()
        # Make the recency signal explicit. The factory builds a pair within
        # microseconds of itself and a fresher touch is worth 25 points, so
        # without this the winner would be decided by clock noise.
        for older_copy, newer_copy in ((first, second), (clock_first, clock_second)):
            AppProject.objects.filter(pk=older_copy.pk).update(
                stars=3, updated_at=timezone.now())
            AppProject.objects.filter(pk=newer_copy.pk).update(
                updated_at=timezone.now() - timedelta(days=1))
        AttentionCase.objects.filter(pk=clock_case.pk).update(
            expires_at=timezone.now() - timedelta(hours=1))

        self.login_owner()
        response = self.client.post(reverse('attention_delegate', args=[case.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(attention.expire_due(), 1)

        case.refresh_from_db()
        clock_case.refresh_from_db()
        self.assertEqual(case.decision_source, 'system')
        self.assertEqual(clock_case.decision_source, 'system')
        self.assertEqual(case.keeper, first)
        self.assertEqual(clock_case.keeper, clock_first,
                         'the deadline must reach the same verdict as the button')
        self.assertIn('Why this one:', case.rationale)
        self.assertIn('Why this one:', clock_case.rationale)

    def test_keep_both_closes_the_case_for_good(self):
        case, first, second = self.open_case()
        self.login_owner()
        response = self.client.post(reverse('attention_dismiss', args=[case.pk]), {'reason': 'not_a_duplicate'})
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.status, 'dismissed')
        self.assertEqual(case.dismissed_reason, 'not_a_duplicate')
        # The next sweep must not re-ask the same question.
        attention.detect_for_user(self.owner)
        self.assertEqual(AttentionCase.objects.count(), 1)

    def test_a_stranger_cannot_see_or_decide_your_case(self):
        case, first, second = self.open_case()
        self.client.login(username='stranger', password='pass12345')
        # 404, not 403: a 403 would confirm the case exists to somebody guessing ids.
        self.assertEqual(self.client.get(reverse('attention_case', args=[case.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('attention_decide', args=[case.pk]),
                                          {'project_id': first.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse('attention_delegate', args=[case.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('attention_reopen', args=[case.pk])).status_code, 404)
        candidate = case.candidates.first()
        self.assertEqual(self.client.post(reverse('attention_delete', args=[case.pk, candidate.pk])).status_code, 404)
        case.refresh_from_db()
        self.assertEqual(case.status, 'open')
        first.refresh_from_db()
        self.assertEqual(first.status, 'published')

    def test_the_decision_endpoints_are_post_only(self):
        case, first, second = self.open_case()
        self.login_owner()
        for name in ('attention_decide', 'attention_delegate', 'attention_reopen', 'attention_dismiss'):
            response = self.client.get(reverse(name, args=[case.pk]))
            self.assertEqual(response.status_code, 405, name)

    def test_the_status_poll_carries_counts_and_no_project_data(self):
        case, first, second = self.open_case()
        self.login_owner()
        response = self.client.get(reverse('attention_status'))
        payload = response.json()
        self.assertEqual(payload['open'], 1)
        self.assertEqual(payload['critical'], 1)
        self.assertEqual(payload['reminder_seconds'], attention.reminder_minutes() * 60)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        body = response.content.decode()
        self.assertNotIn(first.title, body)
        self.assertNotIn(first.slug, body)

    def test_the_status_poll_requires_login(self):
        self.assertEqual(self.client.get(reverse('attention_status')).status_code, 302)

    def test_the_banner_appears_on_other_pages_and_not_on_the_attention_page(self):
        case, first, second = self.open_case()
        self.login_owner()
        feed = self.client.get(reverse('feed'))
        self.assertContains(feed, 'data-attention-banner')
        self.assertContains(feed, 'decision')
        centre = self.client.get(reverse('attention_center'))
        self.assertNotContains(centre, 'data-attention-banner',
                               msg_prefix='the banner is redundant where the decisions live')

    def test_the_banner_is_gone_when_nothing_is_waiting(self):
        self.login_owner()
        response = self.client.get(reverse('feed'))
        self.assertNotContains(response, 'data-attention-banner')

    def test_the_account_menu_links_to_the_attention_centre(self):
        self.open_case()
        self.login_owner()
        response = self.client.get(reverse('feed'))
        self.assertContains(response, reverse('attention_center'))
        self.assertContains(response, 'Needs a decision')

    def test_the_centre_marks_a_system_decision_as_seen(self):
        # Opening the page IS opening the decision — that is what starts the 24h.
        case, first, second = self.open_case()
        attention.decide(case, keeper_project=first, actor=None, source='system')
        self.login_owner()
        self.client.get(reverse('attention_center'))
        case.refresh_from_db()
        self.assertIsNotNone(case.acknowledged_at)
        self.assertIsNotNone(case.final_delete_at)

    def test_the_case_page_shows_the_strategy_and_the_delete_button(self):
        case, first, second = self.open_case()
        attention.decide(case, keeper_project=first, actor=None, source='system')
        self.login_owner()
        response = self.client.get(reverse('attention_case', args=[case.pk]))
        self.assertContains(response, 'Why BlaqVibes chose this one')
        self.assertContains(response, 'Final delete')
        self.assertContains(response, 'Undo — put it back')
        self.assertContains(response, 'Let me choose instead')
        self.assertContains(response, 'Never delete a receipt')

    def test_the_answered_tab_shows_closed_cases(self):
        case, first, second = self.open_case()
        attention.dismiss(case, actor=self.owner, reason='not_a_duplicate')
        self.login_owner()
        response = self.client.get(reverse('attention_center') + '?show=answered')
        self.assertContains(response, case.headline)
        self.assertContains(response, 'Dismissed by the owner')


# ----------------------------------------------------------------------
# 9. The clock, in numbers
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class ClockSettingsTests(AttentionFactories):

    def test_popularity_can_never_outvote_evidence(self):
        # The README says stars do not decide. These are the numbers that make
        # that a fact rather than a slogan: every popularity signal summed at its
        # cap is worth less than one published copy, and less than two of the
        # proof fields a builder writes by hand.
        self.assertLess(attention.MAX_POPULARITY, attention.PUBLISHED_POINTS)
        self.assertLess(attention.MAX_POPULARITY, attention.PROOF_FIELD_POINTS * 2)
        self.assertLess(attention.MAX_POPULARITY, attention.PAID_POINTS)
        self.assertLess(attention.MAX_POPULARITY, attention.REMIX_CHILD_POINTS * attention.REMIX_CHILD_CAP)
        self.assertLess(attention.MAX_POPULARITY, attention.ARTIFACT_POINTS + attention.TRUST_VERIFIED_POINTS)

    def test_the_defaults_are_the_promises_made_in_the_copy(self):
        self.assertEqual(attention.decision_days(), 7)
        self.assertEqual(attention.reminder_minutes(), 30)
        self.assertEqual(attention.final_delete_hours(), 24)
        self.assertEqual(attention.duplicate_threshold(), 70)

    def test_the_settings_move_the_clock_and_the_copy_together(self):
        self.make_duplicate_pair()
        with self.settings(ATTENTION_DECISION_DAYS=3, ATTENTION_REMINDER_MINUTES=5,
                           ATTENTION_FINAL_DELETE_HOURS=6):
            attention.detect_for_user(self.owner)
            case = AttentionCase.objects.get()
            self.assertLess(abs((case.expires_at - case.created_at - timedelta(days=3)).total_seconds()), 5)
            title, body = attention.notification_copy(case)
            self.assertIn('3 days left to decide', body)
            AttentionCase.objects.filter(pk=case.pk).update(
                created_at=timezone.now() - timedelta(minutes=6))
            self.assertEqual(attention.remind_due(), 1, 'the cadence follows the setting')
            attention.decide(case, keeper_project=case.candidates.first().project,
                             actor=self.owner, source='user')
            case.refresh_from_db()
            self.assertLess(abs((case.final_delete_at - case.decided_at - timedelta(hours=6)).total_seconds()), 5)

    def test_the_beat_schedule_uses_the_same_reminder_setting(self):
        from django.conf import settings as django_settings
        entry = django_settings.CELERY_BEAT_SCHEDULE['attention-reminders']
        self.assertEqual(entry['task'], 'gallery.tasks.attention_reminders')
        self.assertEqual(sorted(entry['schedule'].minute), [0, attention.reminder_minutes()])
        self.assertEqual(django_settings.CELERY_BEAT_SCHEDULE['attention-sweep']['task'],
                         'gallery.tasks.attention_sweep')

    def test_summary_counts_what_the_banner_prints(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        summary = attention.summary(self.owner)
        self.assertEqual(summary['open'], 1)
        self.assertEqual(summary['critical'], 1)
        self.assertEqual(summary['oldest_days'], 0)
        self.assertEqual(summary['awaiting_delete'], 0)

    def test_summary_is_empty_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(attention.summary(AnonymousUser())['open'], 0)


# ----------------------------------------------------------------------
# 10. Assets — the stripe has to exist for the sort to mean anything
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class AttentionAssetTests(TestCase):

    def test_the_stylesheet_defines_a_stripe_for_every_category(self):
        from django.conf import settings
        css = (settings.BASE_DIR / 'static' / 'gallery' / 'css' / 'attention.css').read_text()
        for key in CATEGORY_META:
            self.assertIn(f'--cat-{key}:', css, f'{key} has no colour token')
            self.assertIn(f'.bv-notif--{key}', css, f'{key} has no stripe')
            self.assertIn(f'.bv-cat--{key}', css, f'{key} has no word chip')
        self.assertIn('border-left: 4px solid', css, 'the colour must sit on the side of the border')
        self.assertIn('[data-theme="light"]', css, 'light mode needs its own stripe weights')

    def test_the_reminder_script_polls_on_the_cadence(self):
        from django.conf import settings
        js = (settings.BASE_DIR / 'static' / 'gallery' / 'js' / 'attention.js').read_text()
        self.assertIn('data-attention-status-url', js)
        self.assertIn('setInterval', js)
        self.assertIn('visibilitychange', js, 'a throttled background tab still has to nag on return')
        self.assertIn('data-attention-snooze', js)

    def test_the_banner_is_wired_into_the_base_template(self):
        from django.conf import settings
        base = (settings.BASE_DIR / 'templates' / 'gallery' / 'base.html').read_text()
        self.assertIn("gallery/includes/attention_banner.html", base)
        self.assertIn('gallery/css/attention.css', base)
        self.assertIn('gallery/js/attention.js', base)


# ----------------------------------------------------------------------
# 11. The operator command — a dry run has to be the sweep, minus the writing
# ----------------------------------------------------------------------
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', ATTENTION_ENABLED=True)
class AttentionSweepCommandTests(AttentionFactories):

    def run_command(self, *args):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command('attention_sweep', *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_reports_the_case_without_opening_it(self):
        self.make_duplicate_pair()
        out = self.run_command('--dry-run')
        self.assertIn('1 new case(s) would be opened', out)
        self.assertIn('would open', out)
        self.assertEqual(AttentionCase.objects.count(), 0,
                         'a dry run that writes is not a dry run')
        self.assertEqual(Notification.objects.count(), 0,
                         'and it must not tell the owner about a case that does not exist')

    def test_a_dry_run_counts_only_work_the_sweep_would_actually_do(self):
        # The engine refuses to open a second case for a problem that already
        # has one, so the report must refuse to count it too — otherwise an
        # operator reads "15 problems" before a sweep that does nothing, and
        # stops believing the command.
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        out = self.run_command('--dry-run')
        self.assertIn('0 new case(s) would be opened', out)
        self.assertIn('1 already waiting on an owner', out)

    def test_the_sweep_does_exactly_what_the_dry_run_predicted(self):
        self.make_duplicate_pair()
        self.assertIn('1 new case(s) would be opened', self.run_command('--dry-run'))
        self.run_command()
        self.assertEqual(AttentionCase.objects.count(), 1)
        self.assertIn('0 new case(s) would be opened', self.run_command('--dry-run'))

    def test_a_dry_run_sees_the_clocks_the_sweep_enforces(self):
        self.make_duplicate_pair()
        attention.detect_for_user(self.owner)
        case = AttentionCase.objects.get()
        AttentionCase.objects.filter(pk=case.pk).update(
            expires_at=timezone.now() - timedelta(hours=1),
            reminded_at=timezone.now() - timedelta(hours=1))
        out = self.run_command('--dry-run')
        self.assertIn('cases past their 7-day window, would be decided: 1', out)
        self.assertIn('cases due a reminder (every 30 min): 1', out)
