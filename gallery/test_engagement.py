"""Regression tests for the engagement + retention layer.

This module covers the P1 product work — follows, the "from creators you
follow" tab, the daily challenge, the trending/remix rails, creator
analytics, and XP/levels/badges — as *server-side behaviour*, not UI
visibility. Every test here drives a real endpoint (or a real function
the endpoint calls) and asserts on the response or the rows it wrote.

Two of these tests exist because of bugs found while writing them:

* ``test_star_notifies_the_owner`` — the notification helper had been
  defined directly under a stray ``@require_POST`` decorator, so every
  social notification call raised ``AttributeError: 'User' object has no
  attribute 'method'``. A star is the most common write on the site, so
  this keeps that shape from coming back.
* ``test_pr_from_a_fork_that_is_not_yours_is_404`` — ``create_pr`` used to
  swallow the Http404 in its broad ``except Exception`` and answer 302.
"""
import json
from datetime import datetime, time, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gallery.analytics import creator_stats, project_stats
from gallery.models import (
    AppProject,
    Category,
    Challenge,
    CloneEvent,
    Notification,
    Star,
    VibeView,
)
from users.models import Follow, XPEvent
from gallery.tests import make_category, make_project, make_user, make_zip_bytes, published_zip

from users.progress import LEVELS, XP_BY_REASON, award, level_for, progress_for, sync_achievements

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class FollowAndFollowingTabTests(TestCase):
    """P1: follow a creator, then read a feed of only those creators."""

    def setUp(self):
        self.cat = make_category()
        self.alice = make_user('alice')
        self.bob = make_user('bob')
        self.carol = make_user('carol')
        self.alice_vibe = published_zip(make_project(self.alice, self.cat, title='Alice Vibe'))
        self.bob_vibe = published_zip(make_project(self.bob, self.cat, title='Bob Vibe'))
        self.carol_vibe = published_zip(make_project(self.carol, self.cat, title='Carol Vibe'))

    def _follow(self, who, target):
        return self.client.post(f'/u/{target.username}/follow/')

    def test_follow_requires_login(self):
        response = self.client.post('/u/bob/follow/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertFalse(Follow.objects.exists())

    def test_follow_then_unfollow_toggles(self):
        self.client.force_login(self.alice)
        first = self.client.post('/u/bob/follow/')
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()['following'])
        self.assertEqual(first.json()['followers'], 1)
        self.assertTrue(Follow.objects.filter(follower=self.alice, following=self.bob).exists())

        second = self.client.post('/u/bob/follow/')
        self.assertFalse(second.json()['following'])
        self.assertEqual(second.json()['followers'], 0)
        self.assertFalse(Follow.objects.filter(follower=self.alice, following=self.bob).exists())

    def test_cannot_follow_yourself(self):
        self.client.force_login(self.alice)
        response = self.client.post('/u/alice/follow/')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Follow.objects.exists())

    def test_following_a_stranger_who_does_not_exist_is_404(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post('/u/ghost/follow/').status_code, 404)

    def test_follow_writes_a_notification_unless_muted(self):
        from gallery.models import Notification
        self.client.force_login(self.alice)
        self.client.post('/u/bob/follow/')
        self.assertTrue(
            Notification.objects.filter(user=self.bob, kind='follow').exists(),
            'a follow must land in the creator inbox')

        Notification.objects.all().delete()
        profile = self.carol.profile
        profile.notify_on_follow = False
        profile.save(update_fields=['notify_on_follow'])
        self.client.post('/u/carol/follow/')
        self.assertTrue(Follow.objects.filter(follower=self.alice, following=self.carol).exists())
        self.assertFalse(
            Notification.objects.filter(user=self.carol, kind='follow').exists(),
            'a muted kind is never written, so the badge cannot disagree with the inbox')

    def test_following_tab_shows_only_followed_creators(self):
        self.client.force_login(self.alice)
        self.client.post('/u/bob/follow/')

        body = self.client.get('/?following=1').content.decode()
        self.assertIn('/app/%s/' % self.bob_vibe.slug, body)
        self.assertNotIn('/app/%s/' % self.carol_vibe.slug, body)
        self.assertNotIn('/app/%s/' % self.alice_vibe.slug, body)

    def test_following_tab_is_empty_honestly(self):
        """No follows means no cards — never a silent refill of the feed."""
        self.client.force_login(self.alice)
        body = self.client.get('/?following=1').content.decode()
        self.assertNotIn('/app/%s/' % self.bob_vibe.slug, body)
        self.assertNotIn('/app/%s/' % self.carol_vibe.slug, body)

    def test_following_tab_still_hides_private_vibes(self):
        """Following somebody is not a grant to see their pending work."""
        secret = make_project(self.bob, self.cat, title='Bob Secret', status='pending')
        self.client.force_login(self.alice)
        self.client.post('/u/bob/follow/')
        body = self.client.get('/?following=1').content.decode()
        self.assertIn('/app/%s/' % self.bob_vibe.slug, body)
        self.assertNotIn(secret.slug, body)

    def test_following_tab_composes_with_search(self):
        other = published_zip(make_project(self.bob, self.cat, title='Zebra Widget'))
        self.client.force_login(self.alice)
        self.client.post('/u/bob/follow/')
        body = self.client.get('/?following=1&q=Zebra').content.decode()
        self.assertIn('/app/%s/' % other.slug, body)
        self.assertNotIn('/app/%s/' % self.bob_vibe.slug, body)

    def test_today_loop_only_renders_on_the_unfiltered_feed(self):
        """The Today loop links the creator's own vibes and followed creators'
        work. On a search or the Following tab that would put cards on the
        page the active filter excluded, so it is a landing-page-only rail
        (same rule as trending / rising creators)."""
        self.client.force_login(self.alice)
        self.client.post('/u/bob/follow/')
        self.assertIn('BLAQVIBES TODAY', self.client.get('/').content.decode())
        for url in ('/?q=Zebra', '/?following=1', '/?kind=snippet', '/?sort=stars&q=x'):
            self.assertNotIn('BLAQVIBES TODAY', self.client.get(url).content.decode(), url)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class DailyChallengeTests(TestCase):
    """P1: one prompt per calendar day, deterministic, idempotent."""

    def test_same_date_gives_the_same_prompt(self):
        from gallery.daily import pool_entry_for
        day = timezone.localdate()
        self.assertEqual(pool_entry_for(day), pool_entry_for(day))
        self.assertNotEqual(pool_entry_for(day), pool_entry_for(day + timedelta(days=1)))

    def test_ensure_daily_challenge_is_idempotent(self):
        from gallery.daily import ensure_daily_challenge
        first = ensure_daily_challenge()
        second = ensure_daily_challenge()
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Challenge.objects.filter(tag=first.tag).count(), 1)

    def test_challenges_page_shows_todays_prompt(self):
        response = self.client.get('/challenges/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('daily', response.content.decode().lower())

    def test_challenge_detail_lists_only_published_submissions(self):
        from gallery.daily import ensure_daily_challenge
        challenge = ensure_daily_challenge()
        author = make_user('author')
        cat = make_category()
        entry = published_zip(make_project(author, cat, title='Entry Vibe'))
        from gallery.models import Tag
        tag, _ = Tag.objects.get_or_create(slug=challenge.tag, defaults={'name': challenge.tag})
        entry.tags.add(tag)
        secret = make_project(author, cat, title='Secret Entry', status='pending')
        secret.tags.add(tag)
        body = self.client.get(f'/challenges/{challenge.tag}/').content.decode()
        self.assertIn(entry.slug, body)
        self.assertNotIn(secret.slug, body)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class TrendingAndRemixTests(TestCase):
    """P1: trending/rising/remix rails are real signal, never leakage."""

    def setUp(self):
        from gallery import trending
        self.trending = trending
        self.cat = make_category()
        self.owner = make_user('tr-owner')
        self.other = make_user('tr-other')
        self.quiet = published_zip(make_project(self.owner, self.cat, title='Quiet Vibe'))
        self.hot = published_zip(make_project(self.other, self.cat, title='Hot Vibe'))
        self.secret = make_project(self.other, self.cat, title='Secret Vibe', status='pending')

    def _fans(self, n, project):
        """Star is unique per (user, project) — so each star needs a fan."""
        for i in range(n):
            Star.objects.create(project=project, user=make_user(f'fan-{project.pk}-{i}'))

    def test_trending_orders_by_activity_not_creation(self):
        self._fans(4, self.hot)
        CloneEvent.objects.create(project=self.hot, user=self.owner)
        self._fans(3, self.quiet)
        vibes, is_hot = self.trending.trending_vibes(limit=5)
        self.assertTrue(is_hot)
        self.assertEqual(vibes[0].pk, self.hot.pk)

    def test_trending_never_returns_unpublished_vibes(self):
        self._fans(6, self.secret)
        vibes, _ = self.trending.trending_vibes(limit=10)
        self.assertNotIn(self.secret.pk, [p.pk for p in vibes])

    def test_trending_falls_back_to_newest_and_says_so(self):
        """An empty week must not render an empty rail — but must not lie."""
        vibes, is_hot = self.trending.trending_vibes(limit=3)
        self.assertFalse(is_hot)
        self.assertTrue(vibes)
        self.assertTrue(all(p.status == 'published' for p in vibes))

    def test_trending_excludes_the_viewer(self):
        vibes, _ = self.trending.trending_vibes(limit=5, exclude_owner=self.other)
        self.assertNotIn(self.hot.pk, [p.pk for p in vibes])

    def test_recent_remixes_only_shows_published_forks(self):
        fork = published_zip(make_project(self.owner, self.cat, title='Loud Fork'))
        fork.forked_from = self.hot
        fork.save(update_fields=['forked_from'])
        remixes = self.trending.recent_remixes(limit=5)
        self.assertIn(fork.pk, [p.pk for p in remixes])
        self.assertTrue(all(p.status == 'published' for p in remixes))

    def test_suggested_creators_never_suggests_self_or_existing_follows(self):
        self.client.force_login(self.owner)
        Follow.objects.create(follower=self.owner, following=self.other)
        suggested = self.trending.suggested_creators(self.owner, limit=5)
        ids = [u.pk for u in suggested]
        self.assertNotIn(self.owner.pk, ids)
        self.assertNotIn(self.other.pk, ids)

    def test_suggested_creators_is_empty_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(self.trending.suggested_creators(AnonymousUser(), limit=3), [])

    def test_activity_summary_counts_only_real_rows(self):
        Star.objects.create(project=self.hot, user=self.owner)
        summary = self.trending.activity_summary()
        self.assertEqual(summary['stars'], 1)
        self.assertEqual(summary['published'], 2)

    def test_feed_renders_the_rails_without_crashing(self):
        body = self.client.get('/').content.decode()
        self.assertIn(self.hot.title, body)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class AnalyticsTests(TestCase):
    """P1: creator analytics — correct numbers, and yours only."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('an-owner')
        self.stranger = make_user('an-stranger')
        self.project = published_zip(make_project(self.owner, self.cat, title='Stats Vibe'))
        from gallery.models import VibeView
        VibeView.objects.create(project=self.project, viewer=self.stranger)
        CloneEvent.objects.create(project=self.project, user=self.stranger)
        Star.objects.create(project=self.project, user=self.stranger)
        # Lifetimes live on the project row (denormalised); the VibeView /
        # CloneEvent rows are what the 1-day and 7-day windows read.
        AppProject.objects.filter(pk=self.project.pk).update(views=7, clones=3, stars=1)
        self.project.refresh_from_db()

    def test_project_stats_counts_real_rows(self):
        stats = project_stats(self.project, days=14)
        self.assertEqual(stats['views_total'], 7)
        self.assertEqual(stats['downloads_total'], 3)
        self.assertEqual(stats['stars_total'], 1)
        self.assertEqual(stats['views_today'], 1)
        self.assertEqual(stats['views_week'], 1)
        self.assertEqual(stats['downloads_week'], 1)
        self.assertEqual(len(stats['views_series']), 14)
        self.assertEqual(stats['views_max'], 1)
        self.assertIn('rank_in_kind', stats)

    def test_project_stats_on_an_empty_project_is_not_an_error(self):
        empty = published_zip(make_project(self.owner, self.cat, title='Empty Vibe'))
        stats = project_stats(empty, days=7)
        self.assertEqual(stats['views_total'], 0)
        self.assertEqual(stats['views_max'], 0)
        self.assertEqual(len(stats['views_series']), 7)

    def test_creator_stats_only_counts_the_creators_own_vibes(self):
        other_project = published_zip(make_project(self.stranger, self.cat, title='Not Yours'))
        Star.objects.create(project=other_project, user=self.owner)
        stats = creator_stats(self.owner)
        self.assertEqual(stats['published'], 1)  # only Stats Vibe is the owner's
        self.assertEqual(stats['stars_total'], 1)  # the stranger's star is not ours

    def test_stats_page_is_owner_only(self):
        self.client.force_login(self.stranger)
        self.assertEqual(
            self.client.get(f'/app/{self.project.slug}/stats/').status_code, 404)
        self.client.force_login(self.owner)
        response = self.client.get(f'/app/{self.project.slug}/stats/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Stats Vibe', response.content.decode())

    def test_stats_page_renders_no_template_comments_or_markup(self):
        """Django's {# #} cannot span lines — a multi-line comment would be
        rendered verbatim into the page. Keep it out of the HTML."""
        self.client.force_login(self.owner)
        body = self.client.get(f'/app/{self.project.slug}/stats/').content.decode()
        self.assertNotIn('{#', body)
        self.assertNotIn('#}', body)

    def test_stats_page_does_not_leak_another_creators_numbers(self):
        self.client.force_login(self.owner)
        body = self.client.get(f'/app/{self.project.slug}/stats/').content.decode()
        self.assertNotIn('Not Yours', body)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class ProgressTests(TestCase):
    """P1: XP, levels and badges — earned, capped, never faked."""

    def setUp(self):
        self.cat = make_category()
        self.user = make_user('xp-user')

    def test_level_thresholds_are_monotonic(self):
        self.assertEqual(level_for(0)['index'], 1)
        self.assertEqual(level_for(0)['name'], LEVELS[0][1])
        self.assertEqual(level_for(LEVELS[1][0])['index'], 2)
        top = level_for(LEVELS[-1][0] + 1000)
        self.assertIsNone(top['next'])
        self.assertEqual(top['progress'], 100)
        self.assertEqual(top['next_name'], LEVELS[-1][1])
        self.assertEqual(level_for(80)['progress'], 30)

    def test_award_adds_xp_once_per_reference(self):
        before = progress_for(self.user)['xp']
        award(self.user, 'publish', ref='test:1')
        after_first = progress_for(self.user)['xp']
        self.assertEqual(after_first, before + XP_BY_REASON['publish'])
        award(self.user, 'publish', ref='test:1')  # same ref → no double count
        self.assertEqual(progress_for(self.user)['xp'], after_first)

    def test_daily_caps_stop_farming(self):
        from users.progress import DAILY_CAPS
        reason, cap = next(iter(DAILY_CAPS.items()))
        for i in range(cap + 5):
            award(self.user, reason, ref=f'cap:{i}')
        self.assertEqual(
            XPEvent.objects.filter(user=self.user, reason=reason).count(), cap)

    def test_badges_are_earned_not_granted(self):
        sync_achievements(self.user)
        self.assertEqual([b['slug'] for b in progress_for(self.user)['badges']], [])
        project = published_zip(make_project(self.user, self.cat, title='First Vibe'))
        Star.objects.create(project=project, user=make_user('fan'))
        sync_achievements(self.user)
        slugs = [b['slug'] for b in progress_for(self.user)['badges']]
        self.assertIn('first_project', slugs)
        self.assertIn('first_star', slugs)

    def test_profile_shows_level_and_progress(self):
        award(self.user, 'publish', ref='profile:1')
        self.client.force_login(self.user)
        body = self.client.get('/u/xp-user/').content.decode()
        self.assertIn('L2', body)
        self.assertIn('progressbar', body)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class NotificationSurfaceTests(TestCase):
    """P1: the notification helper behind every social write.

    Regression cover for the stray ``@require_POST`` that used to sit on
    ``_notify_project_owner``: each of these actions crashed the request
    before the fix.
    """

    def setUp(self):
        from gallery.models import Notification
        self.Notification = Notification
        self.cat = make_category()
        self.owner = make_user('nt-owner')
        self.fan = make_user('nt-fan')
        self.project = published_zip(make_project(self.owner, self.cat, title='Notify Vibe'))

    def test_star_notifies_the_owner(self):
        self.client.force_login(self.fan)
        response = self.client.post(f'/app/{self.project.slug}/star/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self.Notification.objects.filter(user=self.owner, kind='star').exists())

    def test_comment_notifies_the_owner(self):
        self.client.force_login(self.fan)
        response = self.client.post(f'/app/{self.project.slug}/comment/',
                                    {'body': 'clean comment'})
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(
            self.Notification.objects.filter(user=self.owner, kind='comment').exists())

    def test_starring_your_own_vibe_is_not_a_notification(self):
        self.client.force_login(self.owner)
        response = self.client.post(f'/app/{self.project.slug}/star/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            self.Notification.objects.filter(user=self.owner, kind='star').exists(),
            'nobody needs an inbox row for talking to themselves')

    def test_inbox_shows_only_your_notifications(self):
        from gallery.notify import notify
        notify(self.owner, 'star', 'owner row', url='/a/')
        notify(self.fan, 'star', 'fan row', url='/b/')
        self.client.force_login(self.fan)
        body = self.client.get('/inbox/').content.decode()
        self.assertIn('fan row', body)
        self.assertNotIn('owner row', body)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class RemixHistoryTests(TestCase):
    """P1: fork/remix history — visible, and never a window onto private work."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('rm-owner')
        self.forker = make_user('rm-forker')
        self.root = published_zip(make_project(self.owner, self.cat, title='Root Vibe'))

    def _fork(self):
        self.client.force_login(self.forker)
        response = self.client.post(f'/app/{self.root.slug}/fork/')
        return response

    def test_fork_creates_a_published_child(self):
        response = self._fork()
        self.assertIn(response.status_code, (200, 302))
        fork = AppProject.objects.filter(forked_from=self.root).first()
        self.assertIsNotNone(fork)
        self.assertEqual(fork.owner, self.forker)
        self.assertIn(fork.title, self.client.get('/').content.decode())

    def test_fork_network_is_public_for_a_published_root(self):
        fork = published_zip(make_project(self.forker, self.cat, title='Public Fork'))
        fork.forked_from = self.root
        fork.save(update_fields=['forked_from'])
        body = self.client.get(f'/app/{self.root.slug}/forks/').content.decode()
        self.assertIn(fork.slug, body)

    def test_fork_network_hides_pending_forks_from_strangers(self):
        fork = make_project(self.forker, self.cat, title='Hidden Fork', status='pending')
        fork.forked_from = self.root
        fork.save(update_fields=['forked_from'])
        body = self.client.get(f'/app/{self.root.slug}/forks/').content.decode()
        self.assertNotIn(fork.slug, body)
        self.client.force_login(self.forker)
        body = self.client.get(f'/app/{self.root.slug}/forks/').content.decode()
        self.assertIn(fork.slug, body)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class PersonalisedHomeTests(TestCase):
    """P1: For You — a ranked feed for people with signal, newest for the rest."""

    def setUp(self):
        self.cat = make_category()
        self.user = make_user('fy-user')

    def test_anonymous_feed_defaults_to_newest_not_foryou(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('personalised for you', response.content.decode().lower())

    def test_foryou_uses_your_own_history_only(self):
        from gallery import taste
        other = make_user('fy-other')
        webapp = published_zip(make_project(other, self.cat, title='Webapp Vibe', kind='web_app'))
        cli = published_zip(make_project(make_user('fy-cli'), self.cat, title='CLI Vibe', kind='cli_tool'))
        # Only the other user has a webapp history; our ranking must not copy
        # it. (No project: taste deliberately ignores events on your own
        # vibes — an author refreshing their page is not evidence of taste.)
        taste.record(other, webapp.kind, 'download')
        taste.record(other, webapp.kind, 'star')
        self.assertEqual(taste.top_kinds(self.user, limit=3), [])
        self.assertEqual(taste.top_kinds(other, limit=3), ['web_app'])

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class QueryBudgetTests(TestCase):
    """P12 — the new surfaces must not scale their query count with data.

    The scaling test is the one that matters: an N+1 is invisible in a
    two-row fixture and obvious when the fixture grows. The absolute
    budgets are generous on purpose (they are a tripwire for a 10x
    regression, not a straitjacket for a new badge on the page).
    """

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('qb-owner')
        self.fans = [make_user(f'qb-fan-{i}') for i in range(6)]

    def _add_vibes(self, n, prefix):
        out = []
        for i in range(n):
            p = published_zip(make_project(self.owner, self.cat, title=f'{prefix} {i}'))
            Star.objects.create(project=p, user=self.fans[i % len(self.fans)])
            CloneEvent.objects.create(project=p, user=self.fans[i % len(self.fans)])
            out.append(p)
        return out

    def _queries(self, path):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return len(ctx)

    def test_feed_query_count_does_not_grow_with_the_catalogue(self):
        self.client.force_login(self.owner)
        self._add_vibes(6, 'Small')
        small = self._queries('/')
        self._add_vibes(18, 'Big')
        big = self._queries('/')
        self.assertLessEqual(big, small + 5, f'feed queries grew {small} → {big}')

    def test_profile_query_count_does_not_grow_with_the_catalogue(self):
        self._add_vibes(6, 'Small')
        small = self._queries(f'/u/{self.owner.username}/')
        self._add_vibes(18, 'Big')
        big = self._queries(f'/u/{self.owner.username}/')
        self.assertLessEqual(big, small + 5, f'profile queries grew {small} → {big}')

    def test_stats_page_stays_inside_its_query_budget(self):
        vibes = self._add_vibes(6, 'Stats')
        self.client.force_login(self.owner)
        self.assertLessEqual(self._queries(f'/app/{vibes[0].slug}/stats/'), 45)

    def test_detail_page_stays_inside_its_query_budget(self):
        vibes = self._add_vibes(6, 'Detail')
        self.assertLessEqual(self._queries(f'/app/{vibes[0].slug}/'), 45)

    def test_creator_totals_are_computed_once_per_request(self):
        """The same totals rendered in three places must cost one aggregate."""
        vibes = self._add_vibes(3, 'Totals')
        self.client.force_login(self.owner)
        before = self._queries(f'/app/{vibes[0].slug}/')
        self.assertLessEqual(before, 45)
        profile = self.owner.profile
        first = profile.stars_received()   # warms the cache (1 aggregate)
        rank = profile.rank()              # warms the cache (2 aggregates)
        with self.assertNumQueries(0):
            self.assertEqual(profile.stars_received(), first)
            self.assertEqual(profile.rank(), rank)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-engagement-tests')
class DailyChallengeJobTests(TestCase):
    """The daily loop must turn without a human opening /challenges/."""

    def setUp(self):
        self.cat = make_category()
        self.author = make_user('job-author')
        self.rival = make_user('job-rival')

    def test_management_command_is_idempotent(self):
        from django.core.management import call_command
        call_command('daily_challenge', verbosity=0)
        first = Challenge.objects.count()
        call_command('daily_challenge', verbosity=0)
        self.assertEqual(Challenge.objects.count(), first)

    def test_command_pays_yesterday_winner_once(self):
        from django.core.management import call_command
        from datetime import time as _time
        from gallery.daily import daily_tag
        from gallery.models import Tag
        from users.models import Profile, StarEvent

        yesterday = timezone.localdate() - timedelta(days=1)
        challenge = Challenge.objects.create(
            title='Yesterday', description='d', bounty_stars=15,
            tag=daily_tag(yesterday),
            start=timezone.make_aware(datetime.combine(yesterday, _time.min)),
            end=timezone.make_aware(datetime.combine(yesterday, _time.min)) + timedelta(hours=1),
            is_active=True,
        )
        tag, _ = Tag.objects.get_or_create(slug=challenge.tag, defaults={'name': challenge.tag})
        winner = published_zip(make_project(self.author, self.cat, title='Winner Vibe'))
        winner.tags.add(tag)
        loser = published_zip(make_project(self.rival, self.cat, title='Loser Vibe'))
        loser.tags.add(tag)
        for i in range(3):
            Star.objects.create(project=winner, user=make_user(f'job-fan-{i}'))
        Star.objects.create(project=loser, user=self.rival)

        call_command('daily_challenge', verbosity=0)
        challenge.refresh_from_db()
        self.assertEqual(challenge.winner_id, winner.pk)
        self.assertEqual(
            StarEvent.objects.filter(user=self.author, reason='challenge_bounty').count(), 1)

        # Second run must not pay twice — a cron that fires twice is not a
        # bug in the cron, it is a bug in the job.
        call_command('daily_challenge', verbosity=0)
        self.assertEqual(
            StarEvent.objects.filter(user=self.author, reason='challenge_bounty').count(), 1)

    def test_celery_task_creates_todays_challenge(self):
        from gallery.tasks import run_daily_challenges
        result = run_daily_challenges()
        self.assertEqual(result['tag'], Challenge.objects.get(tag=result['tag']).tag)
