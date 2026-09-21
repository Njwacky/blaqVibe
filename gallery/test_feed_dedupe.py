"""The feed must never show the same vibe twice.

Rails (the trending strip, the Today loop) render on the same page as the
main grid. On a small catalog they used to repeat the grid's cards — every
uploaded app appeared two or three times, which reads exactly like a bug.
The contract now: a project visible in the grid must not appear in any rail
on the same page. These tests pin that from three angles — the trending
helper, the feed view's HTML, and the today-loop tag.
"""
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.template import Context, Template
from django.core.cache import cache
from django.urls import reverse

from gallery.models import AppProject, Category
from gallery.trending import trending_vibes
from users.models import Follow


def published(owner, cat, title, slug=None):
    return AppProject.objects.create(
        owner=owner, category=cat, title=title, slug=slug,
        short_description='Short enough description for the validators.',
        readme='# Title\n\n' + ('Enough readme body text for the checks. ' * 4),
        status='published',
    )


@override_settings(RATELIMIT_ENABLE=False)
class TrendingVibesExcludeIdsTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name='Apps', slug='apps', type='full_app')
        self.a = User.objects.create_user('a', password='x')

    def test_excluded_ids_never_come_back(self):
        vibe = published(self.a, self.cat, 'On the grid')
        rail, _hot = trending_vibes(limit=6, exclude_ids=[vibe.id])
        self.assertNotIn(vibe, rail)

    def test_rail_still_fills_with_the_rest(self):
        on_grid = published(self.a, self.cat, 'On the grid')
        off_grid = published(self.a, self.cat, 'Off the grid')
        rail, _hot = trending_vibes(limit=6, exclude_ids=[on_grid.id])
        self.assertIn(off_grid, rail)
        self.assertNotIn(on_grid, rail)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class FeedDoesNotRepeatCardsTests(TestCase):
    """End-to-end: the rendered feed lists each app exactly once."""

    def setUp(self):
        cache.clear()
        self.cat = Category.objects.create(name='Apps', slug='apps', type='full_app')
        self.owner = User.objects.create_user('catalog-owner', password='x')
        for i in range(3):
            published(self.owner, self.cat, f'Catalog vibe {i}', slug=f'catalog-vibe-{i}')

    def _slugs(self, html):
        import re
        return re.findall(r'/app/([a-z0-9-]+)/', html)

    def test_every_app_appears_once_for_anonymous_visitors(self):
        html = self.client.get(reverse('feed')).content.decode()
        slugs = self._slugs(html)
        self.assertEqual(len(slugs), len(set(slugs)), f'duplicated: {slugs}')
        for i in range(3):
            self.assertIn(f'catalog-vibe-{i}', slugs)

    def test_every_app_appears_once_for_signed_in_visitors(self):
        """Rails + Today loop + grid — still one card per app."""
        viewer = User.objects.create_user('viewer', password='x')
        published(viewer, self.cat, 'Viewer own vibe', slug='viewer-own-vibe')
        self.client.force_login(viewer)
        html = self.client.get(reverse('feed')).content.decode()
        slugs = self._slugs(html)
        self.assertEqual(len(slugs), len(set(slugs)), f'duplicated: {slugs}')
        self.assertNotIn('today-loop__project-title', html.split('WHAT ARE PEOPLE BUILDING')[-1])


class TodayLoopDedupeTests(TestCase):
    """The Today loop renders above the grid: it must not repeat grid cards."""

    def setUp(self):
        cache.clear()
        self.cat = Category.objects.create(name='Apps', slug='apps', type='full_app')
        self.stranger = User.objects.create_user('stranger', password='x')
        self.me = User.objects.create_user('me-today', password='x')
        self.grid_vibe = published(self.stranger, self.cat, 'Already On Grid', slug='already-on-grid')
        self.off_vibe = published(self.stranger, self.cat, 'Only In Rail', slug='only-in-rail')
        self.my_vibe = published(self.me, self.cat, 'My Latest Vibe', slug='my-latest-vibe')

    def _render(self, page_object_list=None):
        request = RequestFactory().get('/')
        request.user = self.me
        context = {'request': request}
        if page_object_list is not None:
            context['page'] = type('Page', (), {'object_list': page_object_list})()
        return Template('{% load today_tags %}{% today_loop %}').render(Context(context))

    def test_grid_vibes_do_not_repeat_in_the_loop(self):
        output = self._render([self.grid_vibe, self.my_vibe])
        self.assertIn('Only In Rail', output)
        self.assertNotIn('Already On Grid', output)
        # "Your latest" is on the grid below — the nudge stands down.
        self.assertNotIn('YOUR LATEST PROJECT', output)

    def test_off_grid_vibe_keeps_the_your_latest_slot(self):
        output = self._render([self.grid_vibe])
        self.assertIn('YOUR LATEST PROJECT', output)
        self.assertIn('My Latest Vibe', output)
        self.assertNotIn('Already On Grid', output)

    def test_without_a_grid_in_context_behaviour_is_unchanged(self):
        output = self._render(None)
        self.assertIn('Already On Grid', output)
        self.assertIn('YOUR LATEST PROJECT', output)

    def test_followed_creators_dedupe_against_the_grid(self):
        Follow.objects.create(follower=self.me, following=self.stranger)
        output = self._render([self.grid_vibe, self.my_vibe])
        self.assertIn('WORTH REMIXING', output)
        self.assertNotIn('Already On Grid', output)


@override_settings(RATELIMIT_ENABLE=False)
class SuperadminShowcaseTests(TestCase):
    """Dev seed: the superadmin profile is the 'have it all' showcase —

    a 100★ wallet and the flagship vibe (100★ received → Gold rank).
    """

    def _seed(self):
        from django.core.management import call_command
        from io import StringIO
        with self.settings(LOCAL_DEV=True, DEBUG=False, SEED_DEMO=False):
            call_command('seed_demo', stdout=StringIO())

    def test_superadmin_gets_the_hundred_star_wallet(self):
        self._seed()
        nolo = User.objects.get(username='nolo.ai')
        self.assertEqual(nolo.profile.stars_balance, 100)
        # Ledgered: balance must equal sum(StarEvent.delta).
        from django.db.models import Sum
        from users.models import StarEvent
        ledger = StarEvent.objects.filter(user=nolo).aggregate(s=Sum('delta'))['s'] or 0
        self.assertEqual(ledger, 100)

    def test_superadmin_owns_the_flagship_and_holds_gold_rank(self):
        from gallery.ranks import contributor_bonus
        self._seed()
        nolo = User.objects.get(username='nolo.ai')
        flagship = AppProject.objects.get(slug='saas-launch-hero-pro')
        self.assertEqual(flagship.owner_id, nolo.pk)
        self.assertEqual(flagship.stars, 100)
        self.assertEqual(flagship.status, 'published')
        self.assertEqual(contributor_bonus(nolo)['name'], 'Gold')

    def test_seed_is_idempotent_and_does_not_steal_the_flagship_back(self):
        self._seed()
        # A real user takes over the flagship (they bought/remixed it, say).
        flagship = AppProject.objects.get(slug='saas-launch-hero-pro')
        someone = User.objects.create_user('flagship-owner', password='x')
        flagship.owner = someone
        flagship.save(update_fields=['owner'])
        self._seed()
        flagship.refresh_from_db()
        # The repair re-flags the showcase to the superadmin — pinned so the
        # showcase never silently rots, but stars are never re-randomised.
        self.assertEqual(flagship.owner_id, User.objects.get(username='nolo.ai').pk)
        self.assertEqual(flagship.stars, 100)
