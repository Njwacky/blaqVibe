"""Quarantine clean-up — search, select, review, delete; the contract, pinned.

What these tests protect:
  1. The held list is bounded, searchable and never leaks email addresses
     (email search is super-admin only, like /admin/roles/).
  2. Every refusal is a per-account sentence, computed by ONE query the page,
     the review and the delete all share: staff, yourself, the ghost account,
     an open appeal, a still-open appeal window, a purchase in flight.
  3. The delete acts on the reviewed ids only, re-checks each account under
     lock, survives a failing chunk by retrying one account at a time, keeps
     sold vibes and receipts, and writes one audit row per account.
  4. The typed phrase, the reason and the permission are enforced on the
     server, whatever the page's script did.
"""
import os
import re
import types
import unicodedata
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.db.models.deletion import ProtectedError
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from gallery.lifecycle import (
    GHOST_USERNAME, get_ghost_user, release_account_projects, release_accounts_projects,
)
from gallery.models import AppProject, PaymentIntent, Trade
from gallery.tests import make_category, make_project

from blaqvibes import envparse

from . import account_cleanup as cleanup
from .models import AdminLog, Profile, QuarantineAppeal, StarEvent, UserQuarantine

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
ALL = dict(RATELIMIT_ENABLE=False, SEED_DEMO=False, EMAIL_BACKEND=LOCMEM, ACCOUNT_CLEANUP_MIN_HOLD_HOURS=72)


def make_user(username, role='user', email=None, **extra):
    """A user without a password hash (the suite is dominated by PBKDF2 otherwise).
    The Profile comes from the post_save signal; the role is set on it."""
    user = User.objects.create(username=username, email=email if email is not None else f'{username}@example.com', **extra)
    Profile.objects.update_or_create(user=user, defaults={'role': role})
    user.refresh_from_db()
    return user


def hold(user, *, reason='spam', hours=100, days=30, source='auto', strikes=1, detail=''):
    """A live hold that started `hours` ago. started_at is auto_now_add, so it
    is back-dated with an UPDATE after the row exists."""
    quarantine = UserQuarantine.objects.create(
        user=user, reason=reason, detail=detail, source=source, strike_count=strikes,
        ends_at=timezone.now() + timedelta(days=days),
    )
    UserQuarantine.objects.filter(pk=quarantine.pk).update(started_at=timezone.now() - timedelta(hours=hours))
    quarantine.refresh_from_db()
    return quarantine


def appeal(quarantine, status='open'):
    return QuarantineAppeal.objects.create(
        quarantine=quarantine, user=quarantine.user, message='I did nothing wrong.', status=status,
    )


def held(actor):
    return {u.username: u for u in cleanup.held_accounts(actor=actor)}


def codes(user):
    return [code for code, _text in cleanup.blockers_for(user)]


@override_settings(**ALL)
class EligibilityTests(TestCase):
    """One sentence per refusal, from the same SQL the delete re-checks."""

    def setUp(self):
        self.admin = make_user('boss', role='admin')
        self.cat = make_category()

    def test_an_old_untouched_hold_is_ready(self):
        hold(make_user('spammer'))
        self.assertEqual(codes(held(self.admin)['spammer']), [])

    def test_a_fresh_hold_waits_for_the_appeal_window_and_says_when(self):
        hold(make_user('newbie'), hours=1)
        user = held(self.admin)['newbie']
        self.assertEqual(codes(user), ['fresh'])
        text = dict(cleanup.blockers_for(user))['fresh']
        self.assertIn('Appeal window open until', text)

    def test_window_is_exactly_the_configured_hours(self):
        hold(make_user('edge-in'), hours=71)
        hold(make_user('edge-out'), hours=73)
        users = held(self.admin)
        self.assertEqual(codes(users['edge-in']), ['fresh'])
        self.assertEqual(codes(users['edge-out']), [])

    @override_settings(ACCOUNT_CLEANUP_MIN_HOLD_HOURS=0)
    def test_window_rule_can_be_switched_off(self):
        hold(make_user('instant'), hours=0)
        self.assertEqual(codes(held(self.admin)['instant']), [])

    def test_an_open_appeal_blocks_but_a_decided_one_does_not(self):
        waiting, denied = make_user('waiting'), make_user('denied')
        appeal(hold(waiting), 'open')
        appeal(hold(denied), 'denied')
        users = held(self.admin)
        self.assertEqual(codes(users['waiting']), ['appeal'])
        self.assertEqual(codes(users['denied']), [])

    def test_staff_accounts_are_never_ready(self):
        for role in ('moderator', 'admin', 'superadmin'):
            hold(make_user(f'is-{role}', role=role))
        hold(make_user('django-staff', is_staff=True))
        hold(make_user('django-super', is_superuser=True))
        for name, user in held(self.admin).items():
            if name != 'boss':
                self.assertIn('staff', codes(user), name)

    def test_you_cannot_delete_yourself(self):
        hold(self.admin)
        self.assertIn('self', codes(held(self.admin)['boss']))

    def test_the_ghost_account_is_protected(self):
        ghost = make_user(GHOST_USERNAME, is_active=False)
        hold(ghost)
        self.assertIn('system', codes(held(self.admin)[GHOST_USERNAME]))

    def test_only_live_holds_are_listed(self):
        lifted, expired, live, never = (make_user(n) for n in ('lifted', 'expired', 'live', 'never'))
        UserQuarantine.objects.filter(pk=hold(lifted).pk).update(status='lifted')
        UserQuarantine.objects.filter(pk=hold(expired).pk).update(ends_at=timezone.now() - timedelta(minutes=1))
        hold(live)
        self.assertEqual(set(held(self.admin)), {'live'})
        self.assertNotIn('never', held(self.admin))
        self.assertIsNotNone(never.pk)

    def test_a_purchase_in_flight_blocks_the_buyer_and_the_seller(self):
        seller, buyer, idle = make_user('seller'), make_user('buyer'), make_user('idle')
        project = make_project(seller, self.cat, title='Selling now', star_cost=0, price_zar=50)
        for user in (seller, buyer, idle):
            hold(user)
        PaymentIntent.objects.create(
            reference='ref-live', buyer=buyer, project=project, amount_zar=50, amount_kobo=5000, status='pending',
        )
        users = held(self.admin)
        self.assertEqual(codes(users['buyer']), ['payment'])
        self.assertEqual(codes(users['seller']), ['payment'])
        self.assertEqual(codes(users['idle']), [])

    def test_an_old_or_settled_checkout_does_not_block(self):
        buyer = make_user('buyer2')
        hold(buyer)
        project = make_project(make_user('owner2'), self.cat, title='Old checkout')
        stale = PaymentIntent.objects.create(
            reference='ref-stale', buyer=buyer, project=project, amount_zar=50, amount_kobo=5000, status='pending',
        )
        PaymentIntent.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(hours=25))
        PaymentIntent.objects.create(
            reference='ref-paid', buyer=buyer, project=project, amount_zar=50, amount_kobo=5000, status='paid',
        )
        self.assertEqual(codes(held(self.admin)['buyer2']), [])


@override_settings(**ALL)
class ListingTests(TestCase):
    def setUp(self):
        self.admin = make_user('boss', role='admin')
        self.root = make_user('root', role='superadmin')
        self.cat = make_category()

    def listing(self, actor=None, **params):
        return cleanup.listing(actor or self.admin, cleanup.parse_filters(params),
                               include_email=cleanup.can_search_email(actor or self.admin))

    def names(self, listing):
        return [row.username for row in listing.rows]

    def test_search_matches_usernames_and_ignores_the_at_sign(self):
        for name in ('kwame', 'kwame2', 'thando'):
            hold(make_user(name))
        self.assertEqual(set(self.names(self.listing(q='@kwam'))), {'kwame', 'kwame2'})
        self.assertEqual(set(self.names(self.listing(q='THANDO'))), {'thando'})

    def test_every_word_must_match(self):
        for name in ('kwame.ghana', 'kwame.za'):
            hold(make_user(name))
        self.assertEqual(self.names(self.listing(q='kwame ghana')), ['kwame.ghana'])

    def test_like_wildcards_typed_into_the_box_are_matched_literally(self):
        # `_` and `%` are LIKE wildcards. If they leaked through, `a_b` would also find
        # `axbxc` and `a%c` would find everything that starts with a and ends with c.
        for name in ('a_b_c', 'axbxc'):
            hold(make_user(name))
        self.assertEqual(self.names(self.listing(q='a_b')), ['a_b_c'])
        self.assertEqual(self.names(self.listing(q='a%c')), [])
        self.assertEqual(self.names(self.listing(q='%%')), [])

    def test_email_search_is_for_super_admins_only(self):
        hold(make_user('kwame', email='kwame@secret-mail.dev'))
        self.assertEqual(self.names(self.listing(self.admin, q='secret-mail')), [])
        self.assertEqual(self.names(self.listing(self.root, q='secret-mail')), ['kwame'])

    def test_a_super_admin_also_matches_a_confirmed_alternative_address(self):
        person = make_user('kwame')
        hold(person)
        EmailAddress.objects.create(user=person, email='kwame@alt.dev', verified=True, primary=False)
        self.assertEqual(self.names(self.listing(self.root, q='alt.dev')), ['kwame'])
        self.assertEqual(self.names(self.listing(self.admin, q='alt.dev')), [])

    def test_reason_show_and_sort_filters(self):
        hold(make_user('zed-spam'), reason='spam')
        hold(make_user('amy-harass'), reason='harassment')
        hold(make_user('mid-fresh'), reason='spam', hours=2)
        self.assertEqual(set(self.names(self.listing(reason='spam'))), {'zed-spam', 'mid-fresh'})
        self.assertEqual(set(self.names(self.listing(show='ready'))), {'zed-spam', 'amy-harass'})
        self.assertEqual(self.names(self.listing(show='blocked')), ['mid-fresh'])
        self.assertEqual(self.names(self.listing(sort='name')), ['amy-harass', 'mid-fresh', 'zed-spam'])

    def test_longest_held_comes_first_by_default(self):
        hold(make_user('recent'), hours=80)
        hold(make_user('ancient'), hours=900)
        self.assertEqual(self.names(self.listing()), ['ancient', 'recent'])
        self.assertEqual(self.names(self.listing(sort='recent')), ['recent', 'ancient'])

    def test_totals_count_the_whole_held_set_not_the_filtered_page(self):
        hold(make_user('ready1'))
        hold(make_user('ready2'))
        hold(make_user('waiting'), hours=1)
        listing = self.listing(q='ready')
        self.assertEqual((listing.held_total, listing.ready_total, listing.blocked_total), (3, 2, 1))
        self.assertEqual(listing.matched, 2)

    def test_reason_dropdown_lists_every_kind_with_its_count(self):
        hold(make_user('s1'), reason='spam')
        counts = {slug: n for slug, _label, n in self.listing().reason_counts}
        self.assertEqual(counts['spam'], 1)
        self.assertEqual(counts['harassment'], 0)
        self.assertEqual(len(counts), 5)

    def test_paging_is_bounded_and_keeps_the_filters_in_its_links(self):
        for i in range(60):
            hold(make_user(f'bot{i:02d}'))
        listing = self.listing(q='bot', per='25')
        self.assertEqual(len(listing.rows), 25)
        self.assertEqual(listing.matched, 60)
        self.assertEqual(listing.page.paginator.num_pages, 3)
        params = listing.filters.params(page=2)
        self.assertEqual(params, {'q': 'bot', 'per': 25, 'page': 2})

    def test_malformed_parameters_fall_back_to_defaults_instead_of_failing(self):
        filters = cleanup.parse_filters({'reason': 'nope', 'show': 'x', 'sort': 'y', 'per': '7', 'page': '-3', 'q': '  @  '})
        self.assertEqual(filters, cleanup.Filters())
        self.assertEqual(cleanup.parse_filters({'per': 'abc', 'page': 'zz'}), cleanup.Filters())

    def test_listing_costs_the_same_queries_for_5_rows_as_for_60(self):
        """No N+1: the avatar, the counts and the blockers all come from the one query."""
        for i in range(5):
            person = make_user(f'few{i}')
            hold(person)
            make_project(person, self.cat, title=f'few vibe {i}')

        def queries():
            with CaptureQueriesContext(connection) as ctx:
                listing = self.listing(per='100')
                for row in listing.rows:
                    _ = (row.profile.avatar, row.blockers, row.vibes_total, row.stars)
            return len(ctx)

        queries()  # warm-up: the first call lazily loads self.admin.profile, once
        few = queries()
        for i in range(55):
            person = make_user(f'many{i}')
            hold(person)
            make_project(person, self.cat, title=f'many vibe {i}')
        self.assertEqual(queries(), few)


@override_settings(**ALL)
class ReviewTests(TestCase):
    def setUp(self):
        self.admin = make_user('boss', role='admin')
        self.cat = make_category()

    def test_review_counts_what_would_be_destroyed_and_what_would_be_kept(self):
        seller = make_user('seller')
        hold(seller)
        Profile.objects.filter(user=seller).update(stars_balance=12)
        sold = make_project(seller, self.cat, title='Sold one', status='published')
        unsold = make_project(seller, self.cat, title='Unsold one', status='published')
        Trade.objects.create(buyer=make_user('customer'), seller=seller, project=sold, cost=3)
        other = make_user('remixer')
        # Survives under the ghost account, so this remix KEEPS its parent.
        make_project(other, self.cat, title='Remix of the sold one', forked_from=sold)
        # The parent is deleted, so this remix LOSES its link — the only one counted.
        make_project(other, self.cat, title='Remix of the unsold one', forked_from=unsold)
        # A remix by the account itself is not "another builder" and dies with it.
        make_project(seller, self.cat, title='Own remix', forked_from=unsold)

        review = cleanup.review(self.admin, [seller.pk])
        self.assertEqual(review.count, 1)
        self.assertEqual((review.vibes_deleted, review.vibes_kept), (2, 1))
        self.assertEqual(review.stars, 12)
        self.assertEqual(review.remixes, 1)
        self.assertEqual(review.phrase, 'DELETE 1')

    def test_review_separates_deletable_from_refused_and_says_why(self):
        ok, waiting, mod = make_user('ok'), make_user('waiting'), make_user('mod', role='moderator')
        hold(ok)
        appeal(hold(waiting))
        hold(mod)
        review = cleanup.review(self.admin, [ok.pk, waiting.pk, mod.pk])
        self.assertEqual([r.username for r in review.rows], ['ok'])
        reasons = {s.label: ' '.join(s.reasons) for s in review.skipped}
        self.assertIn('Appeal waiting', reasons['@waiting'])
        self.assertIn('Staff account', reasons['@mod'])

    def test_ids_that_are_no_longer_held_are_explained_not_ignored(self):
        lifted, gone = make_user('lifted'), make_user('gone')
        UserQuarantine.objects.filter(pk=hold(lifted).pk).update(status='lifted')
        gone_pk = gone.pk
        gone.delete()
        review = cleanup.review(self.admin, [lifted.pk, gone_pk])
        reasons = {s.label: s.reasons[0] for s in review.skipped}
        self.assertIn('No longer in quarantine', reasons['@lifted'])
        self.assertEqual(reasons[f'#{gone_pk}'], 'Already deleted.')

    def test_a_batch_over_the_limit_is_flagged(self):
        with override_settings(ACCOUNT_CLEANUP_MAX_BATCH=3):
            review = cleanup.review(self.admin, [1, 2, 3, 4])
            self.assertTrue(review.over_limit)
            self.assertFalse(cleanup.review(self.admin, [1, 2, 3]).over_limit)

    def test_non_admins_get_an_empty_review(self):
        person = make_user('someone')
        hold(person)
        self.assertEqual(cleanup.review(make_user('mod', role='moderator'), [person.pk]).rows, [])
        self.assertEqual(cleanup.review(person, [person.pk]).rows, [])

    def test_only_a_spam_batch_defaults_the_notice_off(self):
        spam, rude = make_user('spam1'), make_user('rude1')
        hold(spam, reason='spam')
        hold(rude, reason='offensive_language')
        self.assertTrue(cleanup.review(self.admin, [spam.pk]).only_spam)
        self.assertFalse(cleanup.review(self.admin, [spam.pk, rude.pk]).only_spam)

    def test_clean_ids_dedupes_drops_junk_and_stops_reading_past_the_limit(self):
        self.assertEqual(cleanup.clean_ids(['3', '3', 'x', '-1', '0', '', '7', 5], limit=10), [3, 7, 5])
        self.assertEqual(len(cleanup.clean_ids(range(1, 10_000), limit=4)), 5)  # limit + 1 is enough to know


@override_settings(**ALL)
class DeleteTests(TestCase):
    def setUp(self):
        self.admin = make_user('boss', role='admin')
        self.cat = make_category()

    def run_delete(self, users, reason='spam wave, no appeals', confirm=None, notify=False, **kw):
        ids = [u.pk for u in users]
        confirm = confirm if confirm is not None else cleanup.confirm_phrase(len(ids))
        with self.captureOnCommitCallbacks(execute=True):
            return cleanup.delete_quarantined_accounts(
                actor=self.admin, ids=ids, reason=reason, confirm=confirm, notify=notify, **kw)

    # -- the happy path ---------------------------------------------------
    def test_accounts_go_with_their_unsold_vibes_and_personal_data(self):
        person = make_user('spammer')
        hold(person)
        make_project(person, self.cat, title='Spam vibe one')
        make_project(person, self.cat, title='Spam vibe two')
        StarEvent.objects.create(user=person, delta=5, reason='welcome')
        result = self.run_delete([person])
        self.assertTrue(result.ok)
        self.assertEqual(result.deleted, ['@spammer'])
        self.assertFalse(User.objects.filter(username='spammer').exists())
        self.assertFalse(Profile.objects.filter(user_id=person.pk).exists())
        self.assertFalse(AppProject.objects.filter(title__startswith='Spam vibe').exists())
        self.assertFalse(StarEvent.objects.filter(user_id=person.pk).exists())
        self.assertFalse(UserQuarantine.objects.filter(user_id=person.pk).exists())
        self.assertEqual(result.vibes_deleted, 2)

    def test_sold_vibes_and_receipts_survive_under_the_ghost(self):
        seller, buyer = make_user('seller'), make_user('buyer')
        hold(seller)
        sold = make_project(seller, self.cat, title='Paid for', status='published', is_featured=True)
        unsold = make_project(seller, self.cat, title='Nobody bought')
        Trade.objects.create(buyer=buyer, seller=seller, project=sold, cost=3)
        result = self.run_delete([seller])
        self.assertEqual((result.vibes_deleted, result.vibes_kept), (1, 1))
        sold.refresh_from_db()
        self.assertEqual(sold.owner.username, GHOST_USERNAME)
        self.assertEqual(sold.status, 'removed')
        self.assertFalse(sold.is_featured)
        self.assertFalse(AppProject.objects.filter(pk=unsold.pk).exists())
        trade = Trade.objects.get(project=sold)
        self.assertIsNone(trade.seller)
        self.assertEqual(trade.buyer, buyer)

    def test_a_buyers_receipt_survives_the_buyer(self):
        owner, buyer = make_user('owner'), make_user('buyer')
        hold(buyer)
        project = make_project(owner, self.cat, title='Bought earlier')
        Trade.objects.create(buyer=buyer, seller=owner, project=project, cost=2)
        self.run_delete([buyer])
        trade = Trade.objects.get(project=project)
        self.assertIsNone(trade.buyer)
        self.assertEqual(trade.seller, owner)

    def test_remixes_by_other_builders_survive_and_lose_the_link_unless_the_parent_was_sold(self):
        seller, other, buyer = make_user('seller'), make_user('other'), make_user('buyer')
        hold(seller)
        original = make_project(seller, self.cat, title='Original')
        sold = make_project(seller, self.cat, title='Original that sold')
        Trade.objects.create(buyer=buyer, seller=seller, project=sold, cost=1)
        remix = make_project(other, self.cat, title='A remix of it', forked_from=original)
        remix_of_sold = make_project(other, self.cat, title='A remix of the sold one', forked_from=sold)
        self.run_delete([seller])
        remix.refresh_from_db()
        remix_of_sold.refresh_from_db()
        self.assertIsNone(remix.forked_from)                   # its parent is gone
        self.assertEqual(remix_of_sold.forked_from_id, sold.pk)  # its parent lives on under the ghost
        self.assertEqual(remix_of_sold.forked_from.owner.username, GHOST_USERNAME)

    # -- the audit trail ----------------------------------------------------
    def test_one_audit_row_per_account_plus_a_batch_summary(self):
        people = [make_user(f'bot{i}') for i in range(3)]
        for person in people:
            hold(person, reason='spam')
        result = self.run_delete(people, reason='  spam   wave 29 Sept ')
        rows = list(AdminLog.objects.filter(action='delete_account').order_by('id'))
        self.assertEqual(len(rows), 3)
        for row, person in zip(rows, people):
            self.assertEqual(row.actor, self.admin)
            self.assertTrue(row.target.startswith(f'@{person.username}: deleted'))
            self.assertIn('Spam / flooding', row.target)
            self.assertIn(f'[{result.batch}]', row.target)
            self.assertTrue(row.target.endswith('— spam wave 29 Sept'))
        summary = AdminLog.objects.get(action='bulk_delete_accounts')
        self.assertIn('3 deleted, 0 skipped, 0 failed', summary.target)
        self.assertIn(f'[{result.batch}]', summary.target)

    def test_audit_text_is_cut_to_the_column_not_to_a_500(self):
        person = make_user('u' * 40)
        hold(person)
        self.run_delete([person], reason='x' * 500)
        for row in AdminLog.objects.all():
            self.assertLessEqual(len(row.target), 200)

    # -- server-side enforcement ---------------------------------------------
    def test_nothing_is_written_until_everything_checks_out(self):
        person = make_user('careful')
        hold(person)
        cases = [
            dict(confirm='DELETE 2'),
            dict(confirm=''),
            dict(reason='no'),
            dict(reason='     '),
        ]
        for kwargs in cases:
            result = self.run_delete([person], **kwargs)
            self.assertFalse(result.ok, kwargs)
            self.assertTrue(User.objects.filter(pk=person.pk).exists(), kwargs)
        self.assertFalse(AdminLog.objects.exists())

    def test_the_phrase_is_forgiving_about_case_and_spacing_only(self):
        person = make_user('typed')
        hold(person)
        self.assertTrue(self.run_delete([person], confirm='  delete   1 ').ok)

    def test_no_ids_and_too_many_ids_are_refused(self):
        self.assertIn('at least one', self.run_delete([]).error.lower())
        with override_settings(ACCOUNT_CLEANUP_MAX_BATCH=2):
            people = [make_user(f'many{i}') for i in range(3)]
            for p in people:
                hold(p)
            result = self.run_delete(people)
            self.assertIn('At most 2', result.error)
            self.assertEqual(User.objects.filter(username__startswith='many').count(), 3)

    def test_only_admins_can_delete_even_if_a_view_forgot_to_check(self):
        victim = make_user('victim')
        hold(victim)
        for actor in (make_user('plain'), make_user('mod', role='moderator'), None):
            result = cleanup.delete_quarantined_accounts(
                actor=actor, ids=[victim.pk], reason='because', confirm='DELETE 1')
            self.assertIn('Only admins', result.error)
        self.assertTrue(User.objects.filter(pk=victim.pk).exists())

    def test_a_django_superuser_counts_as_an_admin(self):
        boss = make_user('djsuper', is_superuser=True)
        victim = make_user('victim')
        hold(victim)
        result = cleanup.delete_quarantined_accounts(
            actor=boss, ids=[victim.pk], reason='because', confirm='DELETE 1')
        self.assertTrue(result.ok)

    # -- guards at execution --------------------------------------------------
    def test_refused_accounts_are_skipped_and_the_rest_still_go(self):
        ok, waiting, fresh, mod = (make_user(n) for n in ('ok', 'waiting', 'fresh', 'mod2'))
        mod.profile.role = 'moderator'
        mod.profile.save()
        hold(ok)
        appeal(hold(waiting))
        hold(fresh, hours=2)
        hold(mod)
        result = self.run_delete([ok, waiting, fresh, mod])
        self.assertEqual(result.deleted, ['@ok'])
        self.assertEqual({s.label for s in result.skipped}, {'@waiting', '@fresh', '@mod2'})
        self.assertEqual(User.objects.filter(username__in=['waiting', 'fresh', 'mod2']).count(), 3)
        self.assertIn('0 failed', AdminLog.objects.get(action='bulk_delete_accounts').target)
        self.assertIn('3 skipped', AdminLog.objects.get(action='bulk_delete_accounts').target)

    def test_the_review_can_go_stale_and_the_delete_notices(self):
        """An appeal arrives, or another admin lifts the hold, while the
        operator is typing the confirmation."""
        late_appeal, lifted_meanwhile, fine = make_user('late'), make_user('lifted'), make_user('fine')
        q1, q2 = hold(late_appeal), hold(lifted_meanwhile)
        hold(fine)
        people = [late_appeal, lifted_meanwhile, fine]
        self.assertEqual(cleanup.review(self.admin, [p.pk for p in people]).count, 3)
        appeal(q1)
        UserQuarantine.objects.filter(pk=q2.pk).update(status='lifted')
        result = self.run_delete(people)
        self.assertEqual(result.deleted, ['@fine'])
        self.assertEqual(len(result.skipped), 2)
        self.assertTrue(User.objects.filter(username__in=['late', 'lifted']).count() == 2)

    def test_you_cannot_delete_yourself_or_the_ghost_through_a_crafted_request(self):
        hold(self.admin)
        ghost = make_user(GHOST_USERNAME, is_active=False)
        hold(ghost)
        result = self.run_delete([self.admin, ghost])
        self.assertEqual(result.deleted, [])
        self.assertTrue(User.objects.filter(pk__in=[self.admin.pk, ghost.pk]).count() == 2)

    def test_a_second_submit_reports_already_deleted_and_does_not_error(self):
        person = make_user('twice')
        hold(person)
        self.run_delete([person])
        again = cleanup.delete_quarantined_accounts(
            actor=self.admin, ids=[person.pk], reason='double click', confirm='DELETE 1')
        self.assertTrue(again.ok)
        self.assertEqual(again.deleted, [])
        self.assertEqual(again.skipped[0].reasons, ['Already deleted.'])
        self.assertEqual(AdminLog.objects.filter(action='delete_account').count(), 1)

    # -- chunking and failure isolation ---------------------------------------
    def test_large_batches_run_in_chunks(self):
        people = [make_user(f'chunk{i}') for i in range(5)]
        for person in people:
            hold(person)
        with patch.object(cleanup, 'CHUNK_SIZE', 2):
            with patch.object(cleanup, '_delete_chunk', wraps=cleanup._delete_chunk) as spy:
                result = self.run_delete(people)
        self.assertEqual(len(result.deleted), 5)
        self.assertEqual([len(call.kwargs['ids']) for call in spy.call_args_list], [2, 2, 1])
        self.assertEqual(AdminLog.objects.filter(action='delete_account').count(), 5)

    def test_a_failing_chunk_is_retried_one_account_at_a_time(self):
        people = [make_user(f'retry{i}') for i in range(4)]
        for person in people:
            hold(person)
        real = cleanup._delete_chunk

        def flaky(**kwargs):
            if len(kwargs['ids']) > 1:
                raise RuntimeError('deadlock detected')
            return real(**kwargs)

        with patch.object(cleanup, '_delete_chunk', side_effect=flaky):
            result = self.run_delete(people)
        self.assertEqual(len(result.deleted), 4)
        self.assertEqual(result.failed, [])
        self.assertEqual(User.objects.filter(username__startswith='retry').count(), 0)

    def test_one_bad_account_costs_only_itself(self):
        people = [make_user(f'iso{i}') for i in range(4)]
        for person in people:
            hold(person)
        bad = people[2]
        real = cleanup._delete_chunk

        def poisoned(**kwargs):
            if len(kwargs['ids']) > 1 or bad.pk in kwargs['ids']:
                raise RuntimeError('boom')
            return real(**kwargs)

        with patch.object(cleanup, '_delete_chunk', side_effect=poisoned):
            result = self.run_delete(people)
        self.assertEqual(len(result.deleted), 3)
        self.assertEqual([f.label for f in result.failed], [f'@{bad.username}'])
        self.assertTrue(User.objects.filter(pk=bad.pk).exists())
        # No audit row for an account that was not deleted.
        self.assertEqual(AdminLog.objects.filter(action='delete_account').count(), 3)
        self.assertFalse(AdminLog.objects.filter(target__startswith=f'@{bad.username}:').exists())
        self.assertIn('1 failed', AdminLog.objects.get(action='bulk_delete_accounts').target)

    def test_a_payment_landing_mid_delete_is_caught_by_protect_and_the_vibe_is_kept(self):
        """A sale lands after the release step looked and before the delete ran.

        The Trade already exists here (as a payment committed by another
        request would), and the FIRST release is made to miss it — exactly what
        a check-then-act race looks like. The PROTECT constraint fires at the
        delete, the chunk rolls back, and the one-by-one retry looks again,
        sees a paid vibe, and re-homes it instead of destroying the receipt."""
        seller, buyer = make_user('racer'), make_user('racebuyer')
        hold(seller)
        project = make_project(seller, self.cat, title='Sold a second ago')
        Trade.objects.create(buyer=buyer, seller=seller, project=project, cost=1)
        real = cleanup.release_accounts_projects
        calls = {'n': 0}

        def miss_the_first_time(users):
            calls['n'] += 1
            return 0 if calls['n'] == 1 else real(users)

        with patch.object(cleanup, 'release_accounts_projects', side_effect=miss_the_first_time):
            result = self.run_delete([seller])
        self.assertEqual(calls['n'], 2)  # the chunk, then the retry
        self.assertEqual(result.deleted, ['@racer'])
        self.assertEqual(result.failed, [])
        project.refresh_from_db()
        self.assertEqual(project.owner.username, GHOST_USERNAME)
        self.assertEqual(project.status, 'removed')
        self.assertTrue(Trade.objects.filter(project=project, buyer=buyer).exists())
        self.assertFalse(User.objects.filter(username='racer').exists())
        self.assertEqual(AdminLog.objects.filter(action='delete_account').count(), 1)

    def test_protected_error_is_what_the_race_above_really_raises(self):
        seller = make_user('proof')
        project = make_project(seller, self.cat, title='Proof')
        Trade.objects.create(buyer=make_user('proofbuyer'), seller=seller, project=project, cost=1)
        with self.assertRaises(ProtectedError):
            User.objects.filter(pk=seller.pk).delete()

    # -- the notice -----------------------------------------------------------
    def test_each_deleted_person_with_an_address_gets_one_notice(self):
        with_mail, without = make_user('mailed', email='mailed@example.com'), make_user('nomail', email='')
        for person in (with_mail, without):
            hold(person, reason='harassment')
        make_project(with_mail, self.cat, title='v')
        mail.outbox = []
        result = self.run_delete([with_mail, without], notify=True)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['mailed@example.com'])
        self.assertIn('removed', message.subject.lower())
        self.assertIn('@mailed', message.body)
        self.assertIn('Harassment / abuse', message.body)
        self.assertEqual(result.notified, 1)
        self.assertEqual(result.not_notified, 0)

    def test_the_notice_never_carries_the_evidence_or_a_login_link(self):
        from .models import RuleViolation
        person = make_user('rudeone')
        hold(person, reason='offensive_language', detail='a comment')
        RuleViolation.objects.create(user=person, kind='offensive_language', evidence='SECRET-EVIDENCE-TEXT')
        mail.outbox = []
        self.run_delete([person], notify=True)
        body = mail.outbox[0].body + (mail.outbox[0].alternatives[0][0] if mail.outbox[0].alternatives else '')
        self.assertNotIn('SECRET-EVIDENCE-TEXT', body)
        self.assertNotIn('a comment', body)

    def test_notify_off_sends_nothing(self):
        person = make_user('quiet')
        hold(person)
        mail.outbox = []
        self.run_delete([person], notify=False)
        self.assertEqual(mail.outbox, [])

    def test_a_dead_mail_provider_never_undoes_or_fails_the_delete(self):
        person = make_user('unlucky')
        hold(person)
        with patch('users.emails.send_generic_email', side_effect=RuntimeError('smtp down')):
            result = self.run_delete([person], notify=True)
        self.assertEqual(result.deleted, ['@unlucky'])
        self.assertEqual((result.notified, result.not_notified), (0, 1))
        self.assertFalse(User.objects.filter(username='unlucky').exists())

    def test_the_notice_names_the_operator_contact_when_one_is_configured(self):
        from .models import FooterContact
        FooterContact.objects.all().delete()  # a data migration seeds a default contact
        FooterContact.objects.create(kind='email', value='help@blaqvibes.co.za', is_active=True)
        person = make_user('appealer')
        hold(person)
        mail.outbox = []
        self.run_delete([person], notify=True)
        self.assertIn('help@blaqvibes.co.za', mail.outbox[0].body)

    def test_no_notice_is_sent_for_a_rolled_back_delete(self):
        """Sent on commit, never inside the transaction (same rule as roles.py)."""
        person = make_user('rolledback')
        hold(person)
        mail.outbox = []
        result = cleanup.delete_quarantined_accounts(
            actor=self.admin, ids=[person.pk], reason='because', confirm='DELETE 1', notify=True)
        self.assertTrue(result.ok)
        self.assertEqual(mail.outbox, [])  # TestCase never commits, so nothing was sent


@override_settings(**ALL)
class BatchReleaseTests(TestCase):
    """gallery.lifecycle: the batch form the clean-up uses, and the single form it replaced."""

    def setUp(self):
        self.cat = make_category()

    def test_one_call_moves_every_sold_vibe_of_every_account(self):
        a, b, buyer = make_user('a'), make_user('b'), make_user('buyer')
        sold_a = make_project(a, self.cat, title='A sold')
        sold_b = make_project(b, self.cat, title='B sold')
        unsold = make_project(a, self.cat, title='A unsold')
        for project, seller in ((sold_a, a), (sold_b, b)):
            Trade.objects.create(buyer=buyer, seller=seller, project=project, cost=1)
        self.assertEqual(release_accounts_projects([a, b]), 2)
        for project in (sold_a, sold_b):
            project.refresh_from_db()
            self.assertEqual((project.owner.username, project.status), (GHOST_USERNAME, 'removed'))
        unsold.refresh_from_db()
        self.assertEqual(unsold.owner, a)

    def test_the_single_account_form_still_behaves_identically(self):
        owner, buyer = make_user('owner'), make_user('buyer')
        sold = make_project(owner, self.cat, title='Sold')
        Trade.objects.create(buyer=buyer, seller=owner, project=sold, cost=1)
        self.assertEqual(release_account_projects(owner), 1)
        sold.refresh_from_db()
        self.assertEqual(sold.owner.username, GHOST_USERNAME)

    def test_an_empty_batch_does_not_even_create_the_ghost(self):
        self.assertEqual(release_accounts_projects([]), 0)
        self.assertFalse(User.objects.filter(username=GHOST_USERNAME).exists())

    def test_the_batch_costs_the_same_queries_however_many_accounts(self):
        get_ghost_user()  # creating the ghost is a one-off, not part of the per-batch cost
        few = [make_user(f'few{i}') for i in range(2)]
        many = [make_user(f'many{i}') for i in range(12)]
        for user in few + many:
            make_project(user, self.cat, title=f'{user.username} vibe')
        with CaptureQueriesContext(connection) as small:
            release_accounts_projects(few)
        with CaptureQueriesContext(connection) as big:
            release_accounts_projects(many)
        self.assertEqual(len(small), len(big))


@override_settings(**ALL)
class CleanupViewTests(TestCase):
    def setUp(self):
        self.admin = make_user('boss', role='admin')
        self.root = make_user('root', role='superadmin')
        self.mod = make_user('mod', role='moderator')
        self.plain = make_user('plain')
        self.cat = make_category()
        self.list_url = reverse('quarantined_accounts')
        self.review_url = reverse('quarantined_review')
        self.delete_url = reverse('quarantined_delete')

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    # -- who may open it ------------------------------------------------------
    def test_anonymous_is_sent_to_sign_in(self):
        for url in (self.list_url, self.review_url, self.delete_url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn('next=', response.url)

    def test_only_admins_and_up_can_open_any_of_the_three_urls(self):
        for user in (self.plain, self.mod):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.list_url).status_code, 403, user)
            self.assertEqual(self.client.post(self.review_url, {'ids': ['1']}).status_code, 403, user)
            self.assertEqual(self.client.post(self.delete_url, {'ids': ['1']}).status_code, 403, user)
        for user in (self.admin, self.root):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.list_url).status_code, 200, user)

    def test_review_and_delete_are_post_only(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.review_url).status_code, 405)
        self.assertEqual(self.client.get(self.delete_url).status_code, 405)

    def test_a_moderator_posting_a_delete_changes_nothing(self):
        victim = make_user('victim')
        hold(victim)
        self.client.force_login(self.mod)
        self.client.post(self.delete_url, {'ids': [victim.pk], 'reason': 'because', 'confirm': 'DELETE 1'})
        self.assertTrue(User.objects.filter(pk=victim.pk).exists())

    def test_csrf_is_enforced_on_the_destructive_post(self):
        victim = make_user('victim')
        hold(victim)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        response = strict.post(self.delete_url, {'ids': [victim.pk], 'reason': 'because', 'confirm': 'DELETE 1'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=victim.pk).exists())

    # -- the list -------------------------------------------------------------
    def test_ready_rows_have_a_checkbox_and_blocked_rows_say_why(self):
        ready, waiting, fresh = make_user('readyone'), make_user('waitingone'), make_user('freshone')
        hold(ready)
        appeal(hold(waiting))
        hold(fresh, hours=3)
        self.client.force_login(self.admin)
        body = self.client.get(self.list_url).content.decode()
        self.assertIn(f'name="ids" value="{ready.pk}"', body)
        self.assertNotIn(f'value="{waiting.pk}"', body)
        self.assertNotIn(f'value="{fresh.pk}"', body)
        self.assertIn('Appeal waiting', body)
        self.assertIn('Appeal window open until', body)
        self.assertIn('csrfmiddlewaretoken', body)
        self.assertIn(f'action="{self.review_url}"', body)

    def test_the_page_never_prints_an_email_address(self):
        hold(make_user('kwame', email='kwame.private@example.com'))
        for user in (self.admin, self.root):
            self.client.force_login(user)
            self.assertNotContains(self.client.get(self.list_url), 'kwame.private@example.com')

    def test_search_hint_tells_each_role_what_it_can_search(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.list_url), 'placeholder="@username"')
        self.client.force_login(self.root)
        self.assertContains(self.client.get(self.list_url), 'placeholder="@name or email"')

    def test_search_via_the_url_narrows_the_list(self):
        for name in ('alpha-bot', 'beta-bot'):
            hold(make_user(name))
        self.client.force_login(self.admin)
        body = self.client.get(self.list_url, {'q': 'alpha'}).content.decode()
        self.assertIn('@alpha-bot', body)
        self.assertNotIn('@beta-bot', body)

    def test_the_script_gets_only_the_results_block_and_a_marker(self):
        hold(make_user('partial-one'))
        self.client.force_login(self.admin)
        response = self.client.get(self.list_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response['X-QA-Partial'], '1')
        body = response.content.decode()
        self.assertIn('@partial-one', body)
        self.assertNotIn('<html', body)
        self.assertNotIn('qa-filters', body)
        full = self.client.get(self.list_url)
        self.assertNotIn('X-QA-Partial', full)
        self.assertIn('<html', full.content.decode())

    def test_the_browser_may_never_reuse_the_fragment_as_the_page(self):
        """Regression: live search, leave, press Back rendered the bare results
        fragment (no nav, no CSS) because the HTTP cache keyed the script's
        partial and the full page by the same URL."""
        hold(make_user('cached-one'))
        self.client.force_login(self.admin)
        for extra in ({}, {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}):
            response = self.client.get(self.list_url, {'q': 'cached'}, **extra)
            self.assertIn('no-store', response['Cache-Control'], extra)
            self.assertIn('private', response['Cache-Control'], extra)
            self.assertIn('X-Requested-With', response['Vary'], extra)

    def test_empty_states_explain_themselves(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.list_url), 'Nothing is quarantined right now.')
        hold(make_user('someone'))
        self.assertContains(self.client.get(self.list_url, {'q': 'zzzz'}), 'No held account matches.')

    def test_pager_links_keep_the_search(self):
        for i in range(30):
            hold(make_user(f'bot{i:02d}'))
        self.client.force_login(self.admin)
        body = self.client.get(self.list_url, {'q': 'bot', 'per': '25'}).content.decode()
        self.assertIn('class="qa-pager"', body)
        self.assertRegex(body, r'href="/admin/quarantined/\?[^"]*q=bot[^"]*page=2')

    def test_a_hand_edited_url_is_never_a_500(self):
        hold(make_user('someone'))
        self.client.force_login(self.admin)
        for params in ({'page': '999'}, {'page': 'abc'}, {'per': '0'}, {'sort': 'DROP TABLE'}, {'reason': '<script>'}):
            self.assertEqual(self.client.get(self.list_url, params).status_code, 200, params)

    def test_hold_detail_is_escaped(self):
        hold(make_user('xss'), detail='<img src=x onerror=alert(1)>')
        self.client.force_login(self.admin)
        body = self.client.get(self.list_url).content.decode()
        self.assertNotIn('<img src=x onerror', body)
        self.assertIn('&lt;img src=x', body)

    # -- review ---------------------------------------------------------------
    def test_review_shows_the_phrase_the_impact_and_only_deletable_ids(self):
        ok, waiting = make_user('okone'), make_user('waitone')
        hold(ok)
        appeal(hold(waiting))
        make_project(ok, self.cat, title='Soon gone')
        self.client.force_login(self.admin)
        response = self.client.post(self.review_url, {'ids': [ok.pk, waiting.pk]})
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Delete 1 account?', body)
        self.assertIn('DELETE 1', body)
        self.assertIn(f'name="ids" value="{ok.pk}"', body)
        self.assertNotIn(f'name="ids" value="{waiting.pk}"', body)
        self.assertIn('@waitone', body)
        self.assertIn('Appeal waiting', body)
        self.assertIn('vibe', body)
        self.assertIn('Nothing has been changed yet', body)
        self.assertTrue(User.objects.filter(pk__in=[ok.pk, waiting.pk]).count() == 2)

    def test_review_with_nothing_deletable_offers_no_confirm_form(self):
        waiting = make_user('onlywaiting')
        appeal(hold(waiting))
        self.client.force_login(self.admin)
        body = self.client.post(self.review_url, {'ids': [waiting.pk]}).content.decode()
        self.assertIn('Nothing to delete', body)
        self.assertNotIn('data-qa-confirm-form', body)

    def test_review_without_a_selection_bounces_back_with_a_message(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.review_url, {})
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertIn('Select at least one account', ' '.join(self.messages(response)))

    def test_review_over_the_batch_limit_bounces_back(self):
        self.client.force_login(self.admin)
        with override_settings(ACCOUNT_CLEANUP_MAX_BATCH=2):
            response = self.client.post(self.review_url, {'ids': ['1', '2', '3']})
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertIn('more than 2', ' '.join(self.messages(response)))

    def test_spam_only_batches_default_the_email_notice_off(self):
        bot, rude = make_user('botone'), make_user('rudeone')
        hold(bot, reason='spam')
        hold(rude, reason='harassment')
        self.client.force_login(self.admin)
        spam_page = self.client.post(self.review_url, {'ids': [bot.pk]}).content.decode()
        mixed_page = self.client.post(self.review_url, {'ids': [bot.pk, rude.pk]}).content.decode()
        tick = re.compile(r'<input id="qa-notify" type="checkbox" name="notify"( checked)?>')
        self.assertIsNone(tick.search(spam_page).group(1))
        self.assertEqual(tick.search(mixed_page).group(1), ' checked')

    # -- delete ---------------------------------------------------------------
    def test_the_full_flow_deletes_reports_and_clears_the_selection(self):
        people = [make_user(f'gone{i}') for i in range(3)]
        for person in people:
            hold(person)
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.delete_url, {
                'ids': [p.pk for p in people], 'reason': 'spam wave, no appeals', 'confirm': 'DELETE 3',
            })
        self.assertRedirects(response, f'{self.list_url}?cleared=1', fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(username__startswith='gone').count(), 0)
        joined = ' '.join(self.messages(response))
        self.assertIn('Deleted 3 accounts', joined)
        self.assertRegex(joined, r'Logged as batch [0-9a-f]{6}')
        follow = self.client.get(f'{self.list_url}?cleared=1')
        self.assertContains(follow, 'data-reset="1"')

    def test_a_wrong_phrase_rerenders_the_review_and_keeps_what_was_typed(self):
        person = make_user('keepme')
        hold(person)
        self.client.force_login(self.admin)
        response = self.client.post(self.delete_url, {
            'ids': [person.pk], 'reason': 'my careful reason', 'confirm': 'DELETE 9', 'notify': 'on',
        })
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('my careful reason', body)
        self.assertIn('Type DELETE 1 exactly', ' '.join(self.messages(response)))
        self.assertIn(f'name="ids" value="{person.pk}"', body)
        self.assertRegex(body, r'name="notify" checked>')
        self.assertTrue(User.objects.filter(pk=person.pk).exists())

    def test_a_delete_of_nothing_left_redirects_instead_of_rendering_an_empty_review(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.delete_url, {'reason': 'because', 'confirm': 'DELETE 0'})
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)

    def test_skips_and_failures_are_reported_in_words(self):
        ok, waiting = make_user('okay'), make_user('holdup')
        hold(ok)
        q = hold(waiting)
        self.client.force_login(self.admin)
        review = self.client.post(self.review_url, {'ids': [ok.pk, waiting.pk]})
        self.assertEqual(review.status_code, 200)
        appeal(q)  # arrives after the review
        response = self.client.post(self.delete_url, {
            'ids': [ok.pk, waiting.pk], 'reason': 'clean up', 'confirm': 'DELETE 2',
        })
        joined = ' '.join(self.messages(response))
        self.assertIn('Deleted 1 account ', joined)
        self.assertIn('Skipped 1: @holdup', joined)
        self.assertIn('Appeal waiting', joined)

    def test_the_hourly_rate_limit_stops_a_runaway_script_with_a_message_not_a_403(self):
        person = make_user('limited')
        hold(person)
        self.client.force_login(self.admin)
        post = {'ids': [person.pk], 'reason': 'x', 'confirm': 'nope'}
        with override_settings(RATELIMIT_ENABLE=True):
            from django.core.cache import caches
            for alias in ('default', 'ratelimit'):
                try:
                    caches[alias].clear()
                except Exception:
                    pass
            first_thirty = {self.client.post(self.delete_url, post).status_code for _ in range(30)}
            over = self.client.post(self.delete_url, post)
        self.assertEqual(first_thirty, {200})  # refused confirmations re-render the review
        self.assertRedirects(over, self.list_url, fetch_redirect_response=False)
        self.assertIn('Too many deletions', ' '.join(self.messages(over)))
        self.assertTrue(User.objects.filter(pk=person.pk).exists())

    # -- links and the audit page --------------------------------------------
    def test_admins_see_the_door_moderators_do_not(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('appeals_queue')), self.list_url)
        self.assertContains(self.client.get(reverse('admin_dashboard')), self.list_url)
        self.client.force_login(self.mod)
        self.assertNotContains(self.client.get(reverse('appeals_queue')), self.list_url)

    def test_the_audit_page_pages_so_a_big_batch_cannot_bury_older_decisions(self):
        for i in range(120):
            AdminLog.objects.create(actor=self.admin, action='delete_account', target=f'@bot{i:03d}: deleted')
        self.client.force_login(self.admin)
        first = self.client.get(reverse('audit_log'))
        self.assertEqual(len(first.context['logs']), 50)
        self.assertContains(first, 'Older →')
        self.assertContains(first, '@bot119')
        third = self.client.get(reverse('audit_log'), {'page': 3})
        self.assertEqual(len(third.context['logs']), 20)
        self.assertContains(third, '@bot000')
        self.assertContains(third, '← Newer')
        self.assertEqual(self.client.get(reverse('audit_log'), {'page': 'zzz'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('audit_log'), {'page': 99}).status_code, 200)

    def test_delete_entries_render_on_the_audit_page(self):
        person = make_user('audited')
        hold(person)
        self.client.force_login(self.admin)
        self.client.post(self.delete_url, {'ids': [person.pk], 'reason': 'audit me please', 'confirm': 'DELETE 1'})
        body = self.client.get(reverse('audit_log')).content.decode()
        self.assertIn('delete_account', body)
        self.assertIn('@audited', body)
        self.assertIn('bulk_delete_accounts', body)


def printable_only(text):
    return not [ch for ch in text if not ch.isspace() and unicodedata.category(ch).startswith('C')]


@override_settings(**ALL)
class HostileInputTests(TestCase):
    """What a script, a tampered form or a careless paste can send.

    None of it may reach the database as it arrived, raise past the view (the
    role decorator turns any escaping exception into a misleading 403 page),
    or land in the audit log as text the next admin cannot see.
    """

    def setUp(self):
        self.admin = make_user('boss', role='admin')
        self.root = make_user('root', role='superadmin')
        self.list_url = reverse('quarantined_accounts')
        self.review_url = reverse('quarantined_review')
        self.delete_url = reverse('quarantined_delete')
        self.client.force_login(self.admin)

    def run_delete(self, people, reason, *, notify=False):
        ids = [p.pk for p in people]
        with self.captureOnCommitCallbacks(execute=True):
            return cleanup.delete_quarantined_accounts(
                actor=self.admin, ids=ids, reason=reason,
                confirm=cleanup.confirm_phrase(len(ids)), notify=notify,
            )

    # -- ids ------------------------------------------------------------------
    def test_ids_are_plain_ascii_digits_inside_the_database_range(self):
        _low, high = connection.ops.integer_field_range(User._meta.pk.get_internal_type())
        raw = [
            '12', ' 13 ', '00017',                      # fine (whitespace and zeros are harmless)
            '1_4', '+15', '-16', '0', '12.5', '0x1f',   # int() would take some of these; a form never sends them
            '\u0661\u0662\u0663',                        # Arabic-Indic digits: int() reads them as 123
            '', None, True, 12.0, '9' * 40,
            str(high + 1),                               # one past the largest id the database can hold
            str(high),
        ]
        self.assertEqual(cleanup.clean_ids(raw, limit=50), [12, 13, 17, high])

    def test_ids_that_can_not_exist_never_reach_a_query_or_crash_a_page(self):
        junk = ['9' * 30, '-1', 'abc', '<script>', '\u0661\u0662']
        self.admin.profile  # the admin check reads it once; load it so the count below is about the ids
        with CaptureQueriesContext(connection) as queries:
            review = cleanup.review(self.admin, junk)
        self.assertEqual((review.requested, review.count, review.skipped), (0, 0, []))
        self.assertEqual(len(queries), 0)
        for url in (self.review_url, self.delete_url):
            response = self.client.post(url, {'ids': junk, 'reason': 'because', 'confirm': 'DELETE 1'})
            self.assertRedirects(response, self.list_url, fetch_redirect_response=False)

    # -- the search box ----------------------------------------------------------
    def test_the_search_drops_control_and_invisible_characters(self):
        self.assertEqual(cleanup.parse_filters({'q': '\x00kw\x07am\u202ee\u200b'}).query, 'kwame')
        self.assertEqual(cleanup.parse_filters({'q': '  @\x00 Kw\x1bame  '}).query, 'Kwame')
        hold(make_user('kwame'))
        page = self.client.get(self.list_url, {'q': '@\x00kwa\x07me'})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '@kwame')
        self.assertNotIn('\x00', page.content.decode())

    # -- the reason -------------------------------------------------------------------
    def test_the_reason_is_plain_printable_text_before_it_is_audited(self):
        person = make_user('spammer')
        hold(person)
        result = self.run_delete([person], 'spam\x00 wave\u202e  29\n\tSept\x1b[0m\u200b')
        self.assertTrue(result.ok, result.error)
        row = AdminLog.objects.get(action='delete_account')
        self.assertTrue(row.target.endswith('— spam wave 29 Sept[0m'), row.target)
        for log in AdminLog.objects.all():
            self.assertTrue(printable_only(log.target), repr(log.target))

    def test_an_invisible_reason_is_no_reason(self):
        person = make_user('quiet1')
        hold(person)
        result = self.run_delete([person], '\u200b' * 8 + '\x00' * 4 + '\u202e' * 4)
        self.assertFalse(result.ok)
        self.assertIn('reason', result.error.lower())
        self.assertTrue(User.objects.filter(pk=person.pk).exists())
        self.assertFalse(AdminLog.objects.filter(action__in=['delete_account', 'bulk_delete_accounts']).exists())

    def test_ordinary_text_passes_through_untouched(self):
        for text in ('Ñomvula — ünïcode ✓ 🤖 spam', 'spam wave 29 Sept, no appeals', 'ẞtraße Mağaza İstanbul'):
            self.assertEqual(cleanup.clean_text(text, limit=100), text)
        self.assertEqual(cleanup.clean_text('a' * 300, limit=100), 'a' * 100)
        self.assertEqual(cleanup.clean_text('word ' * 40, limit=100), ('word ' * 40)[:100].rstrip())
        self.assertEqual(cleanup.clean_text(None, limit=10), '')

    # -- responses ------------------------------------------------------------------------
    def test_review_and_delete_responses_are_never_cached(self):
        person = make_user('ghostwriter')
        hold(person)
        shown = self.client.post(self.review_url, {'ids': [person.pk]})
        self.assertEqual(shown.status_code, 200)
        self.assertIn('no-store', shown['Cache-Control'])
        refused = self.client.post(self.delete_url, {'ids': [person.pk], 'reason': 'because', 'confirm': 'nope'})
        self.assertEqual(refused.status_code, 200)
        self.assertIn('no-store', refused['Cache-Control'])
        done = self.client.post(self.delete_url, {'ids': [person.pk], 'reason': 'because', 'confirm': 'DELETE 1'})
        self.assertEqual(done.status_code, 302)
        self.assertIn('no-store', done['Cache-Control'])
        self.assertIn('no-store', self.client.get(self.list_url)['Cache-Control'])

    def test_the_largest_allowed_batch_still_posts(self):
        # 500 ids + token + reason + phrase: well inside Django's 1000-field limit, which
        # would answer a bigger form with a bare 400 before this view ever ran.
        with override_settings(ACCOUNT_CLEANUP_MAX_BATCH=500):
            response = self.client.post(self.review_url, {'ids': [str(n) for n in range(1, 501)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['review'].requested, 500)

    # -- a name that breaks the profile URL -------------------------------------------------
    def test_a_username_that_cannot_be_linked_does_not_take_the_page_down(self):
        odd = make_user('slash/name')  # sign-up would refuse it; an import or a shell would not
        hold(odd)
        page = self.client.get(self.list_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '@slash/name')
        self.assertContains(page, f'value="{odd.pk}"')  # and it can still be selected and cleaned up

    # -- output encoding -----------------------------------------------------------------------------
    def test_a_hostile_username_is_escaped_everywhere_it_is_printed(self):
        """List, review, flash message, audit page: the name is data, never markup."""
        evil = make_user('"><img src=x onerror=alert(1)>', email='')
        hold(evil, detail='<script>alert(2)</script>')
        waiting = make_user('<b>waiting</b>')
        appeal(hold(waiting))   # refused: its name appears in the "Skipped" flash
        markup = ('<img src=x', '<script>alert', '<b>waiting</b>')

        def assert_inert(response, where):
            body = response.content.decode()
            for fragment in markup:
                self.assertNotIn(fragment, body, f'{where}: {fragment!r} was printed unescaped')

        listing = self.client.get(self.list_url)
        assert_inert(listing, 'list')
        self.assertContains(listing, '&lt;b&gt;waiting&lt;/b&gt;')
        review = self.client.post(self.review_url, {'ids': [evil.pk, waiting.pk]})
        assert_inert(review, 'review')
        self.assertContains(review, '&lt;b&gt;waiting&lt;/b&gt;')
        done = self.client.post(
            self.delete_url,
            {'ids': [evil.pk, waiting.pk], 'reason': 'hostile names', 'confirm': 'DELETE 2'},
            follow=True,
        )
        assert_inert(done, 'flash message')
        self.assertContains(done, 'Skipped 1')
        self.assertContains(done, '&lt;b&gt;waiting&lt;/b&gt;')
        assert_inert(self.client.get(reverse('audit_log')), 'audit log')
        self.assertFalse(User.objects.filter(pk=evil.pk).exists())

    # -- logs ------------------------------------------------------------------------------------
    def test_a_username_cannot_forge_a_log_line(self):
        person = make_user('evil\nFORGED error line')
        hold(person)
        with patch('users.emails.send_generic_email', side_effect=RuntimeError('smtp down')):
            with self.assertLogs('users.account_cleanup', level='INFO') as logs:
                self.run_delete([person], 'testing the logs', notify=True)
        self.assertTrue(logs.records)
        for record in logs.records:
            self.assertNotIn('\n', record.getMessage(), record.getMessage())


@override_settings(**ALL)
class TunablesTests(TestCase):
    """Every number an operator might reasonably change is a setting — and each
    setting really drives the behaviour it names."""

    def setUp(self):
        self.admin = make_user('boss', role='admin')
        self.cat = make_category()

    def test_the_payment_window_follows_its_setting(self):
        buyer = make_user('checkout')
        hold(buyer)
        project = make_project(make_user('vendor'), self.cat, title='Checkout me')
        intent = PaymentIntent.objects.create(
            reference='ref-30h', buyer=buyer, project=project, amount_zar=50, amount_kobo=5000, status='pending',
        )
        PaymentIntent.objects.filter(pk=intent.pk).update(created_at=timezone.now() - timedelta(hours=30))
        with override_settings(ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS=24):
            self.assertEqual(codes(held(self.admin)['checkout']), [])
        with override_settings(ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS=48):
            user = held(self.admin)['checkout']
            self.assertEqual(codes(user), ['payment'])
            self.assertIn('48 h', dict(cleanup.blockers_for(user))['payment'])

    def test_the_notice_budget_follows_its_setting(self):
        def batch(prefix, budget):
            people = [make_user(f'{prefix}{i}') for i in range(3)]
            for person in people:
                hold(person)
            ticks = iter(range(10, 10_000, 10))   # the clock reads 0, then 10, 20, 30 ... seconds
            clock = types.SimpleNamespace(monotonic=lambda: next(ticks) - 10)
            mail.outbox = []
            with override_settings(ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS=budget), patch.object(cleanup, 'time', clock):
                with self.captureOnCommitCallbacks(execute=True):
                    result = cleanup.delete_quarantined_accounts(
                        actor=self.admin, ids=[p.pk for p in people], reason='budget check',
                        confirm='DELETE 3', notify=True,
                    )
            self.assertEqual(len(result.deleted), 3, 'the delete itself never depends on the notices')
            return result.notified, result.not_notified, len(mail.outbox)

        # Looks at 10 s, 20 s, 30 s: a 20 s budget still sends the first two; a 6 s budget sends none.
        self.assertEqual(batch('roomy', 20), (2, 1, 2))
        self.assertEqual(batch('tight', 6), (0, 3, 0))

    def test_the_rates_reach_the_views(self):
        person = make_user('ratey')
        hold(person)
        client = Client()
        client.force_login(self.admin)
        review, delete = reverse('quarantined_review'), reverse('quarantined_delete')
        with override_settings(RATELIMIT_ENABLE=True, ACCOUNT_CLEANUP_REVIEW_RATE='2/h', ACCOUNT_CLEANUP_DELETE_RATE='1/h'):
            from django.core.cache import caches
            for alias in ('default', 'ratelimit'):
                try:
                    caches[alias].clear()
                except Exception:
                    pass
            self.assertEqual([client.post(review, {'ids': [person.pk]}).status_code for _ in range(2)], [200, 200])
            over = client.post(review, {'ids': [person.pk]})
            self.assertRedirects(over, reverse('quarantined_accounts'), fetch_redirect_response=False)
            self.assertIn('Too many requests', ' '.join(str(m) for m in get_messages(over.wsgi_request)))
            wrong = {'ids': [person.pk], 'reason': 'because', 'confirm': 'nope'}
            self.assertEqual(client.post(delete, wrong).status_code, 200)
            limited = client.post(delete, wrong)
            self.assertRedirects(limited, reverse('quarantined_accounts'), fetch_redirect_response=False)
            self.assertIn('Too many deletions', ' '.join(str(m) for m in get_messages(limited.wsgi_request)))
        self.assertTrue(User.objects.filter(pk=person.pk).exists())


class EnvParseTests(SimpleTestCase):
    """settings.py reads its numbers through blaqvibes/envparse.py."""

    def env(self, **values):
        return patch.dict(os.environ, values)

    def test_a_whole_number_is_read_trimmed_and_clamped(self):
        with self.env(X_N=' 7 '):
            self.assertEqual(envparse.env_int('X_N', 3, minimum=1, maximum=10), 7)
        with self.env(X_N='99'):
            self.assertEqual(envparse.env_int('X_N', 3, minimum=1, maximum=10), 10)
        with self.env(X_N='-4'):
            self.assertEqual(envparse.env_int('X_N', 3, minimum=1, maximum=10), 1)

    def test_unset_or_blank_means_the_default(self):
        os.environ.pop('X_N', None)
        self.assertEqual(envparse.env_int('X_N', 3, minimum=1, maximum=10), 3)
        with self.env(X_N='   '):
            self.assertEqual(envparse.env_int('X_N', 3, minimum=1, maximum=10), 3)

    def test_anything_that_is_not_a_whole_number_stops_the_boot_and_names_the_variable(self):
        for bad in ('lots', '7.5', '1_0', '\u0663', '5 6', '0x10'):
            with self.env(X_N=bad), self.assertRaises(ImproperlyConfigured) as caught:
                envparse.env_int('X_N', 3, minimum=1, maximum=10)
            self.assertIn('X_N', str(caught.exception), bad)
            self.assertIn(repr(bad), str(caught.exception), bad)

    def test_a_rate_is_normalised_and_junk_is_refused(self):
        self.assertEqual(envparse.normalize_rate('30/h'), '30/h')
        self.assertEqual(envparse.normalize_rate(' 50 / 2H '), '50/2h')
        self.assertEqual(envparse.normalize_rate('5/m'), '5/m')
        for bad in ('30 per hour', '0/h', '', None, 'x/h', '30/w', '30', '-5/h', '30/h; drop', '1234567/h', 5, '30/'):
            self.assertIsNone(envparse.normalize_rate(bad), bad)
        with self.env(X_R=' 10 / h '):
            self.assertEqual(envparse.env_rate('X_R', '30/h'), '10/h')
        os.environ.pop('X_R', None)
        self.assertEqual(envparse.env_rate('X_R', '30/h'), '30/h')
        with self.env(X_R='30 per hour'), self.assertRaises(ImproperlyConfigured) as caught:
            envparse.env_rate('X_R', '30/h')
        self.assertIn('X_R', str(caught.exception))

    def test_settings_uses_these_readers_for_every_cleanup_knob(self):
        source = Path(settings.BASE_DIR, 'blaqvibes', 'settings.py').read_text()
        for name in (
            'ACCOUNT_CLEANUP_MIN_HOLD_HOURS', 'ACCOUNT_CLEANUP_MAX_BATCH', 'ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS',
            'ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS', 'ACCOUNT_CLEANUP_REVIEW_RATE', 'ACCOUNT_CLEANUP_DELETE_RATE',
        ):
            self.assertRegex(source, rf"{name}\s*=\s*env_(int|rate)\(\s*'{name}'", name)
            self.assertIn(f'{name}=', Path(settings.BASE_DIR, '.env.example').read_text(), f'{name} missing from .env.example')


class AssetTests(SimpleTestCase):
    """The page's files follow the project's layout rules (see test_mobile_layouts)."""

    @staticmethod
    def read(*parts):
        return Path(settings.BASE_DIR, *parts).read_text()

    def css(self):
        return re.sub(r'/\*.*?\*/', '', self.read('static', 'gallery', 'css', 'quarantine-cleanup.css'), flags=re.S)

    def test_the_stylesheet_fixes_overflow_instead_of_clipping_it(self):
        body = self.css()
        self.assertIn('minmax(0, 1fr)', body)
        self.assertIn('min-width: 0', body)
        self.assertIn('overflow-wrap: anywhere', body)
        self.assertNotRegex(body, r'overflow(?:-x)?\s*:\s*(hidden|clip)')

    def test_touch_targets_and_input_sizes_suit_a_phone(self):
        body = self.css()
        self.assertIn('min-height: 44px', body)
        self.assertRegex(body, r'\.qa-row__pick\s*\{[^}]*width: 44px;[^}]*height: 44px;')
        self.assertRegex(body, r'\.qa-confirm input\[type="text"\][^{]*\{[^}]*font-size: 16px')
        self.assertIn('@media (min-width: 1080px)', body)

    def test_blocked_rows_are_not_styled_away_to_unreadable(self):
        self.assertNotRegex(self.css(), r'\.qa-row--blocked\s*\{[^}]*opacity')

    def test_the_action_bar_keeps_its_controls_clear_of_the_floating_feedback_button(self):
        self.assertRegex(self.css(), r'\.qa-bar\s*\{[^}]*justify-content: flex-start;')

    def test_templates_load_the_external_files_and_carry_no_inline_code(self):
        for name in ('quarantined_accounts.html', 'quarantined_review.html', '_quarantined_results.html'):
            source = self.read('templates', 'users', name)
            self.assertNotRegex(source, r'<script(?![^>]*\bsrc=)', name)
            self.assertNotIn(' style="', source, name)
            self.assertNotRegex(source, r'\son[a-z]+="', name)
        for name in ('quarantined_accounts.html', 'quarantined_review.html'):
            source = self.read('templates', 'users', name)
            self.assertIn("gallery/css/quarantine-cleanup.css", source)
            self.assertIn("gallery/js/quarantine-cleanup.js", source)

    def test_search_focus_is_for_a_mouse_not_a_phone_keyboard(self):
        self.assertNotIn('autofocus', self.read('templates', 'users', 'quarantined_accounts.html'))
        script = self.read('static', 'gallery', 'js', 'quarantine-cleanup.js')
        self.assertIn('(hover: hover) and (pointer: fine)', script)

    def test_the_script_has_no_dependencies_and_keeps_server_side_truth(self):
        script = self.read('static', 'gallery', 'js', 'quarantine-cleanup.js')
        self.assertIn('X-QA-Partial', script)
        self.assertIn('sessionStorage', script)
        self.assertNotIn('eval(', script)
        self.assertNotIn('innerHTML = ' + 'selected', script)

    def test_the_script_trusts_only_plain_ids_from_session_storage(self):
        script = self.read('static', 'gallery', 'js', 'quarantine-cleanup.js')
        body = script[script.index('function readSelection'):script.index('function writeSelection')]
        self.assertIn('Array.isArray(raw)', body)
        self.assertIn('/^[0-9]{1,19}$/', body)
        self.assertIn('slice(0, max)', body)


@override_settings(**ALL)
class SettingsTests(SimpleTestCase):
    def test_defaults(self):
        with override_settings():
            del settings.ACCOUNT_CLEANUP_MIN_HOLD_HOURS
            del settings.ACCOUNT_CLEANUP_MAX_BATCH
            self.assertEqual(cleanup.min_hold_hours(), 72)
            self.assertEqual(cleanup.max_batch(), 100)

    def test_values_are_clamped_and_junk_falls_back(self):
        with override_settings(ACCOUNT_CLEANUP_MAX_BATCH=10_000, ACCOUNT_CLEANUP_MIN_HOLD_HOURS=-5):
            self.assertEqual(cleanup.max_batch(), 500)
            self.assertEqual(cleanup.min_hold_hours(), 0)
        with override_settings(ACCOUNT_CLEANUP_MAX_BATCH='lots', ACCOUNT_CLEANUP_MIN_HOLD_HOURS=None):
            self.assertEqual(cleanup.max_batch(), 100)
            self.assertEqual(cleanup.min_hold_hours(), 72)

    def test_the_phrase_carries_the_count(self):
        self.assertEqual(cleanup.confirm_phrase(12), 'DELETE 12')

    def test_staff_roles_follow_the_role_ladder_so_a_new_role_is_protected_by_default(self):
        from .roles import ROLE_ORDER
        above_user = {role for role, rank in ROLE_ORDER.items() if rank > ROLE_ORDER['user']}
        self.assertEqual(set(cleanup.STAFF_ROLES), above_user)
        self.assertTrue({'moderator', 'admin', 'superadmin'} <= set(cleanup.STAFF_ROLES))
        self.assertNotIn('user', cleanup.STAFF_ROLES)

    def test_every_tunable_has_a_default_when_the_setting_is_absent(self):
        with override_settings():
            for name in (
                'ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS', 'ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS',
                'ACCOUNT_CLEANUP_REVIEW_RATE', 'ACCOUNT_CLEANUP_DELETE_RATE',
            ):
                delattr(settings, name)
            self.assertEqual(cleanup.payment_hold_hours(), 24)
            self.assertEqual(cleanup.notice_budget_seconds(), 6)
            self.assertEqual((cleanup.review_rate(), cleanup.delete_rate()), ('120/h', '30/h'))

    def test_the_new_numbers_are_clamped_and_junk_falls_back(self):
        with override_settings(ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS=10_000, ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS=999):
            self.assertEqual(cleanup.payment_hold_hours(), 24 * 30)
            self.assertEqual(cleanup.notice_budget_seconds(), 20)
        with override_settings(ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS=0, ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS=0):
            self.assertEqual(cleanup.payment_hold_hours(), 1)   # 0 would switch the payment guard off
            self.assertEqual(cleanup.notice_budget_seconds(), 1)
        with override_settings(ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS='soon', ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS=None):
            self.assertEqual(cleanup.payment_hold_hours(), 24)
            self.assertEqual(cleanup.notice_budget_seconds(), 6)

    def test_rates_are_validated_and_junk_falls_back(self):
        with override_settings(ACCOUNT_CLEANUP_DELETE_RATE='5/m', ACCOUNT_CLEANUP_REVIEW_RATE=' 50 / 2h '):
            self.assertEqual((cleanup.delete_rate(), cleanup.review_rate()), ('5/m', '50/2h'))
        for junk in ('30 per hour', '0/h', '', None, 'x/h', '30/w', '30/h; drop'):
            with override_settings(ACCOUNT_CLEANUP_DELETE_RATE=junk, ACCOUNT_CLEANUP_REVIEW_RATE=junk):
                self.assertEqual((cleanup.delete_rate(), cleanup.review_rate()), ('30/h', '120/h'), junk)

    def test_the_batch_cap_fits_inside_djangos_form_field_limit(self):
        # Every id is one POST field. Raise DATA_UPLOAD_MAX_NUMBER_FIELDS or lower the cap, not both ways.
        limit = settings.DATA_UPLOAD_MAX_NUMBER_FIELDS
        self.assertTrue(limit is None or limit >= 500 + 10, limit)

    def test_django_ratelimit_asks_for_the_rate_on_every_request(self):
        with override_settings(ACCOUNT_CLEANUP_DELETE_RATE='7/m'):
            self.assertEqual(cleanup.delete_rate_for('group', None), '7/m')
            self.assertEqual(cleanup.review_rate_for('group', None), '120/h')
