"""Account quarantine + appeals — the contract, pinned.

What these tests protect:
  1. A profanity refusal is no longer silent: the attempt is recorded, the
     person is told (in line, inbox, email), and the account is held 30 days.
  2. The hold blocks NEW posts (comment, publish) but never reading — and it
     lifts by itself when the clock passes.
  3. A person can appeal, staff see the appeal, and the decision reaches the
     person. Deny keeps the hold; accept lifts it immediately.
  4. Staff can apply the same hold for any other rule breach, by hand.
  5. Staff-only pages stay staff-only.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from gallery.models import Comment, Notification
from gallery.tests import make_category, make_project, make_user
from users.models import AdminLog, QuarantineAppeal, RuleViolation, UserQuarantine
from users.quarantine import (
    active_quarantine,
    is_quarantined,
    quarantine_user,
    record_violation,
)

OBSCENE = 'this is fucking broken and you are an asshole'


@override_settings(
    RATELIMIT_ENABLE=False,
    SEED_DEMO=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    QUARANTINE_DAYS=30,
    QUARANTINE_STRIKES=1,
)
class QuarantineFlowTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.offender = make_user('offender')
        self.moderator = make_user('mod', role='moderator')
        self.project = make_project(self.owner, self.cat, title='Clean vibe')
        mail.outbox = []

    def _post_obscene_comment(self):
        self.client.login(username='offender', password='pass12345')
        return self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': OBSCENE},
        )

    # -- 1. the breach is not silent -------------------------------------
    def test_obscene_comment_is_refused_recorded_and_quarantines(self):
        response = self._post_obscene_comment()
        self.assertEqual(response.status_code, 302)

        # Nothing was posted.
        self.assertEqual(Comment.objects.count(), 0)

        # The breach is on record with its evidence (staff need it for appeals).
        violation = RuleViolation.objects.get(user=self.offender)
        self.assertEqual(violation.kind, 'offensive_language')
        self.assertEqual(violation.surface, 'comment')
        self.assertIn('fucking', violation.evidence)
        self.assertTrue(violation.quarantined)

        # The person knows: 30-day hold, reason, and where to appeal.
        quarantine = active_quarantine(self.offender)
        self.assertIsNotNone(quarantine)
        self.assertEqual(quarantine.reason, 'offensive_language')
        self.assertGreaterEqual(quarantine.days_left(), 29)
        self.assertLessEqual(quarantine.days_left(), 31)

        notice = Notification.objects.filter(user=self.offender, kind='account_quarantine').first()
        self.assertIsNotNone(notice)
        self.assertIn('quarantined', notice.title.lower())
        self.assertEqual(notice.url, '/quarantine/')

        # …and by email, to the address on the account.
        to_offender = [m for m in mail.outbox if m.to == [self.offender.email]]
        self.assertTrue(to_offender, 'the quarantined person must be emailed')
        self.assertIn('quarantined', to_offender[0].subject.lower())

        # Staff are notified too — in app and by email.
        staff_note = Notification.objects.filter(user=self.moderator, kind='account_quarantine').first()
        self.assertIsNotNone(staff_note)
        self.assertIn('@offender', staff_note.title)
        self.assertTrue([m for m in mail.outbox if m.to == [self.moderator.email]])

    def test_clean_comment_is_never_a_violation(self):
        self.client.login(username='offender', password='pass12345')
        response = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'Does this work with Django 5 class-based views?'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(RuleViolation.objects.count(), 0)
        self.assertFalse(is_quarantined(self.offender))

    def test_anonymous_refusal_is_not_a_violation(self):
        response = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': OBSCENE},
        )
        self.assertEqual(response.status_code, 302)  # bounced to login
        self.assertEqual(RuleViolation.objects.count(), 0)
        self.assertEqual(UserQuarantine.objects.count(), 0)

    def test_the_offending_words_are_never_emailed_or_in_a_title(self):
        """Notifications must not carry the slur into an inbox."""
        self._post_obscene_comment()
        for message in mail.outbox:
            self.assertNotIn('fucking', (message.subject or '').lower())
            self.assertNotIn('fucking', (message.body or '').lower())
        for notification in Notification.objects.all():
            self.assertNotIn('fucking', notification.title.lower())
            self.assertNotIn('fucking', notification.body.lower())

    # -- 2. enforcement ---------------------------------------------------
    def test_quarantined_account_cannot_comment_but_can_still_read(self):
        quarantine_user(self.offender, reason='offensive_language', detail='test hold')
        self.client.login(username='offender', password='pass12345')

        # Reading is never blocked, and the notice page is reachable.
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get(f'/app/{self.project.slug}/').status_code, 200)
        self.assertEqual(self.client.get('/quarantine/').status_code, 200)

        blocked = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'A perfectly polite comment that should still be stopped'},
        )
        self.assertRedirects(blocked, '/quarantine/')
        self.assertEqual(Comment.objects.count(), 0)

    def test_quarantined_account_cannot_publish(self):
        quarantine_user(self.offender, reason='spam', detail='test hold')
        self.client.login(username='offender', password='pass12345')
        response = self.client.post('/publish/', {
            'title': 'A brand new vibe',
            'short_description': 'A short description of the vibe for the feed.',
            'readme': '# A new vibe\n\n' + ('Enough words to pass the readme length rule. ' * 5),
            'tech_stack': 'Django',
            'creator_kind': '',
            'build_choice': '',
            'star_cost': 0,
            'price_zar': 0,
        })
        self.assertRedirects(response, '/quarantine/')
        self.assertEqual(self.offender.projects.count(), 0)

    def test_hold_lifts_by_itself_when_the_clock_passes(self):
        quarantine = quarantine_user(self.offender, reason='offensive_language', detail='test hold')
        quarantine.ends_at = timezone.now() - timedelta(minutes=1)
        quarantine.save(update_fields=['ends_at'])

        self.assertFalse(is_quarantined(self.offender))
        self.client.login(username='offender', password='pass12345')
        response = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'Sorry about that — back and keeping it civil.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Comment.objects.count(), 1)

    def test_repeat_breach_while_held_does_not_stack_days(self):
        quarantine = quarantine_user(self.offender, reason='offensive_language', detail='first hold')
        ends_before = quarantine.ends_at
        record_violation(self.offender, kind='offensive_language', surface='comment',
                         detail='again', evidence=OBSCENE + ' again')
        quarantine.refresh_from_db()
        self.assertEqual(quarantine.ends_at, ends_before)
        self.assertEqual(RuleViolation.objects.filter(user=self.offender).count(), 1)
        self.assertEqual(UserQuarantine.objects.filter(user=self.offender).count(), 1)

    def test_duplicate_submit_is_one_violation(self):
        record_violation(self.offender, kind='offensive_language', surface='comment',
                         detail='first', evidence=OBSCENE)
        record_violation(self.offender, kind='offensive_language', surface='comment',
                         detail='double submit', evidence=OBSCENE)
        self.assertEqual(RuleViolation.objects.filter(user=self.offender).count(), 1)

    def test_raising_the_threshold_warns_instead_of_quarantining(self):
        with override_settings(QUARANTINE_STRIKES=2):
            report = record_violation(self.offender, kind='offensive_language',
                                      surface='comment', detail='warn', evidence=OBSCENE)
            self.assertFalse(report['quarantined'])
            self.assertIsNone(active_quarantine(self.offender))
            # …and the warning is spoken, not silent.
            notice = Notification.objects.filter(user=self.offender, kind='account_quarantine').first()
            self.assertIsNotNone(notice)
            self.assertIn('warning', notice.title.lower())

            report = record_violation(self.offender, kind='offensive_language',
                                      surface='comment', detail='again', evidence='something else entirely')
            self.assertTrue(report['quarantined'])
            self.assertIsNotNone(active_quarantine(self.offender))

    # -- 3. appeals -------------------------------------------------------
    def test_person_can_appeal_and_staff_see_it(self):
        quarantine_user(self.offender, reason='offensive_language', detail='test hold')
        self.client.login(username='offender', password='pass12345')

        response = self.client.post('/quarantine/', {
            'message': 'I quoted a song lyric about a broken build — I meant no harm, sorry.',
        })
        self.assertRedirects(response, '/quarantine/')
        appeal = QuarantineAppeal.objects.get(user=self.offender)
        self.assertEqual(appeal.status, 'open')

        # The moderator is told at once (inbox + email).
        appealed_note = Notification.objects.filter(user=self.moderator, kind='appeal').first()
        self.assertIsNotNone(appealed_note)
        self.assertIn('Appeal', appealed_note.title)
        self.assertTrue([m for m in mail.outbox if m.to == [self.moderator.email]])

        # …and the appeal shows on the staff page with the evidence.
        self.client.logout()
        self.client.login(username='mod', password='pass12345')
        queue = self.client.get('/moderation/appeals/')
        self.assertEqual(queue.status_code, 200)
        self.assertContains(queue, 'song lyric')
        self.assertContains(queue, '@offender')

    def test_second_appeal_is_blocked_while_one_is_open(self):
        quarantine_user(self.offender, reason='spam', detail='test hold')
        self.client.login(username='offender', password='pass12345')
        self.client.post('/quarantine/', {'message': 'First appeal — it really was a misunderstanding.'})
        self.client.post('/quarantine/', {'message': 'Second appeal — please please please look.'})
        self.assertEqual(QuarantineAppeal.objects.filter(user=self.offender).count(), 1)

    def test_accept_lifts_the_hold_and_tells_the_person(self):
        quarantine_user(self.offender, reason='offensive_language', detail='test hold')
        self.client.login(username='offender', password='pass12345')
        self.client.post('/quarantine/', {'message': 'It was a misunderstanding, I promise.'})
        appeal = QuarantineAppeal.objects.get()
        self.client.logout()

        self.client.login(username='mod', password='pass12345')
        response = self.client.post(f'/moderation/appeals/{appeal.id}/', {
            'decision': 'accept',
            'note': 'Read the context — no harm meant.',
        })
        self.assertEqual(response.status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, 'accepted')
        self.assertFalse(is_quarantined(self.offender))

        decision_note = Notification.objects.filter(user=self.offender, kind='appeal').first()
        self.assertIsNotNone(decision_note)
        self.assertIn('accepted', decision_note.title.lower())

        # The person can post again straight away.
        self.client.logout()
        self.client.login(username='offender', password='pass12345')
        self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'Thanks for reading it properly.'},
        )
        self.assertEqual(Comment.objects.count(), 1)

    def test_deny_keeps_the_hold_and_explains(self):
        quarantine_user(self.offender, reason='harassment', detail='test hold')
        self.client.login(username='offender', password='pass12345')
        self.client.post('/quarantine/', {'message': 'That was not harassment, it was a joke.'})
        appeal = QuarantineAppeal.objects.get()
        self.client.logout()

        self.client.login(username='mod', password='pass12345')
        self.client.post(f'/moderation/appeals/{appeal.id}/', {
            'decision': 'deny',
            'note': 'The thread shows otherwise.',
        })
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, 'denied')
        self.assertTrue(is_quarantined(self.offender))
        decision_note = Notification.objects.filter(user=self.offender, kind='appeal').first()
        self.assertIn('stands', decision_note.title.lower())
        self.assertIn('thread shows otherwise', decision_note.body)

    def test_extend_adds_days(self):
        quarantine = quarantine_user(self.offender, reason='spam', detail='test hold')
        before = quarantine.ends_at
        self.client.login(username='mod', password='pass12345')
        self.client.post(f'/moderation/quarantines/{quarantine.id}/', {
            'action': 'extend',
            'note': 'Second campaign already running.',
        })
        quarantine.refresh_from_db()
        self.assertAlmostEqual(
            (quarantine.ends_at - before).total_seconds(), 30 * 86400, delta=60,
        )

    # -- 4. staff-applied holds ------------------------------------------
    def test_staff_can_quarantine_for_any_other_rule_breach(self):
        self.client.login(username='mod', password='pass12345')
        response = self.client.post('/moderation/quarantines/new/', {
            'username': '@offender',
            'kind': 'harassment',
            'days': '30',
            'detail': 'Repeated personal attacks in trade notes.',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(is_quarantined(self.offender))
        self.assertTrue(
            AdminLog.objects.filter(action='quarantine_user', target__contains='offender').exists()
        )
        notice = Notification.objects.filter(user=self.offender, kind='account_quarantine').first()
        self.assertIsNotNone(notice)
        self.assertIn('harassment', notice.body.lower())

    def test_staff_cannot_quarantine_a_moderator_or_themselves(self):
        self.client.login(username='mod', password='pass12345')
        self.client.post('/moderation/quarantines/new/', {
            'username': 'mod', 'kind': 'other', 'detail': 'oops',
        })
        self.assertFalse(is_quarantined(self.moderator))
        self.assertEqual(UserQuarantine.objects.count(), 0)

    def test_lift_from_the_queue_notifies_the_person(self):
        quarantine = quarantine_user(self.offender, reason='spam', detail='test hold')
        self.client.login(username='mod', password='pass12345')
        self.client.post(f'/moderation/quarantines/{quarantine.id}/', {
            'action': 'lift', 'note': 'Wrong account — our mix-up.',
        })
        self.assertFalse(is_quarantined(self.offender))
        notice = Notification.objects.filter(user=self.offender, kind='account_quarantine').first()
        self.assertIn('lifted', notice.title.lower())

    # -- 5. the notice is visible everywhere it needs to be ---------------
    def test_banner_shows_on_every_page_for_a_held_account(self):
        quarantine_user(self.offender, reason='offensive_language', detail='test hold')
        self.client.login(username='offender', password='pass12345')
        page = self.client.get('/')
        self.assertContains(page, 'Your account is quarantined until')
        self.assertContains(page, '/quarantine/')

    def test_notice_page_shows_the_reason_countdown_and_evidence(self):
        self._post_obscene_comment()
        page = self.client.get('/quarantine/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Offensive language')
        self.assertContains(page, 'Ends')
        self.assertContains(page, 'Send appeal to the moderators')
        # The person can see what they actually wrote — that is what makes an
        # appeal possible instead of a guess.
        self.assertContains(page, 'fucking')

    def test_moderation_queue_links_the_appeals_queue(self):
        quarantine_user(self.offender, reason='spam', detail='test hold')
        self.client.login(username='mod', password='pass12345')
        page = self.client.get('/moderation/queue/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Account quarantine')

    # -- 5. staff-only pages ---------------------------------------------
    def test_appeals_queue_is_staff_only(self):
        self.client.login(username='offender', password='pass12345')
        self.assertEqual(self.client.get('/moderation/appeals/').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/moderation/appeals/').status_code, 302)  # login

    def test_regular_user_cannot_post_a_staff_quarantine(self):
        self.client.login(username='offender', password='pass12345')
        self.client.post('/moderation/quarantines/new/', {
            'username': 'owner', 'kind': 'other', 'detail': 'nope',
        })
        self.assertFalse(is_quarantined(self.owner))
        self.assertEqual(UserQuarantine.objects.count(), 0)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class AdminDashboardQuarantineTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.admin = make_user('boss', role='admin')
        self.offender = make_user('offender')

    def test_admin_dashboard_counts_quarantines_and_appeals(self):
        quarantine = quarantine_user(self.offender, reason='offensive_language', detail='hold')
        QuarantineAppeal.objects.create(
            quarantine=quarantine, user=self.offender,
            message='That was a misunderstanding, please review.',
        )
        self.client.login(username='boss', password='pass12345')
        response = self.client.get('/admin/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'OPEN APPEALS')
        self.assertContains(response, 'That was a misunderstanding')

    def test_expired_hold_is_swept_when_staff_open_the_queue(self):
        quarantine = quarantine_user(self.offender, reason='spam', detail='hold')
        quarantine.ends_at = timezone.now() - timedelta(minutes=5)
        quarantine.save(update_fields=['ends_at'])
        self.client.login(username='boss', password='pass12345')
        self.client.get('/moderation/appeals/')
        quarantine.refresh_from_db()
        self.assertEqual(quarantine.status, 'expired')
        self.assertTrue(
            Notification.objects.filter(user=self.offender, kind='account_quarantine').exists()
        )
