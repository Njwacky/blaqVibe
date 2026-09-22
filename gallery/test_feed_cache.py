"""The anonymous feed ID cache must never change what the feed shows.

These tests exist because the cache used to be a correctness hazard rather
than a speed-up:

* The cached page object was not iterable, so `{% for p in page %}` raised
  TypeError inside the template. The view's outer `except` swallowed it and
  rendered the emergency fallback — an empty grid with HTTP 200. Every
  anonymous cache hit showed an empty feed, silently.
* The cache was populated from the unsliced queryset, so pages 2 and 3 were
  cached with page 1's IDs and then served page 1's cards.
* The cached queryset dropped `status='published'`, so a vibe quarantined or
  removed up to 30s earlier kept rendering to anonymous visitors.
* The cache was populated with an extra `values_list('id')[:13]` query that
  inherited the remix_count annotation — a second LEFT JOIN + GROUP BY, about
  10x the cost of the COUNT(*) it was meant to save.

Every test here requests the same URL twice: once cold, once warm. The warm
request is the one that used to be broken.
"""
import re

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .models import AppProject
from .tests import make_category, make_project, make_user

PAGE_SIZE = 12


def card_titles(body):
    """Titles of the vibe cards actually rendered in the grid."""
    return re.findall(r'ellipsis">([^<]*)</div>', body)


def pager_text(body):
    m = re.search(r'font-size:13px">(.*?)</span>', body, re.S)
    return m.group(1).strip() if m else None


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-feed-cache-tests')
class AnonymousFeedCacheTests(TestCase):
    """Anonymous, unfiltered feed — the only path the ID cache may touch."""

    def setUp(self):
        # LocMemCache is shared by every test in the process, so a leaked
        # entry from another module would poison these assertions.
        cache.clear()
        self.cat = make_category()
        self.owner = make_user('cache-builder')
        self.vibes = [
            make_project(self.owner, self.cat, title=f'Vibe {i:02d}')
            for i in range(25)
        ]
        self.newest_first = list(
            AppProject.objects.filter(status='published').order_by('-created_at')
        )

    def expected(self, page):
        lo = (page - 1) * PAGE_SIZE
        return [p.title for p in self.newest_first[lo:lo + PAGE_SIZE]]

    def queries(self, url):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return len(ctx), response.content.decode()

    # ------------------------------------------------------------------
    # The regression that mattered most: a cache hit used to render nothing.
    # ------------------------------------------------------------------
    def test_a_cached_page_still_renders_the_grid(self):
        cold = self.client.get('/').content.decode()
        warm = self.client.get('/').content.decode()
        self.assertEqual(len(card_titles(cold)), PAGE_SIZE)
        self.assertEqual(len(card_titles(warm)), PAGE_SIZE,
                         'a cache hit rendered an empty grid')
        self.assertEqual(card_titles(cold), card_titles(warm))

    def test_a_cached_page_keeps_the_next_link(self):
        self.client.get('/')
        warm = self.client.get('/').content.decode()
        self.assertIn('Next →', warm,
                      'the cached page lost its Next link')
        self.assertIn('Page 1', warm)

    def test_a_cached_page_with_no_next_page_has_no_next_link(self):
        for p in self.newest_first[PAGE_SIZE:]:
            p.delete()
        cache.clear()
        self.client.get('/')
        warm = self.client.get('/').content.decode()
        self.assertEqual(len(card_titles(warm)), PAGE_SIZE)
        self.assertNotIn('Next →', warm)

    # ------------------------------------------------------------------
    # Pages 2 and 3 must not be served page 1's rows.
    # ------------------------------------------------------------------
    def test_cached_page_two_shows_page_two(self):
        cold = self.client.get('/?page=2').content.decode()
        warm = self.client.get('/?page=2').content.decode()
        self.assertEqual(card_titles(cold), self.expected(2))
        self.assertEqual(card_titles(warm), self.expected(2),
                         'a cached page 2 served page 1 content')

    def test_cached_page_three_shows_page_three(self):
        self.client.get('/?page=3')
        warm = self.client.get('/?page=3').content.decode()
        self.assertEqual(card_titles(warm), self.expected(3),
                         'a cached page 3 served page 1 content')

    def test_cached_pages_do_not_repeat_the_first_page(self):
        seen = []
        for page in ('1', '2', '3'):
            self.client.get(f'/?page={page}')           # populate
            seen.append(card_titles(self.client.get(f'/?page={page}').content.decode()))
        self.assertEqual(seen[0], self.expected(1))
        self.assertEqual(seen[1], self.expected(2))
        self.assertEqual(seen[2], self.expected(3))
        self.assertEqual(len(set(map(tuple, seen))), 3,
                         'cached pages rendered the same cards')

    def test_the_cached_entry_records_the_page_it_belongs_to(self):
        self.client.get('/?page=2')
        entry = cache.get('feed:anon:page:2:sort:newest:v3')
        self.assertIsNotNone(entry, 'nothing was cached for page 2')
        self.assertEqual(entry['ids'], [p.id for p in self.newest_first[12:24]])
        self.assertEqual(entry['number'], 2)
        self.assertTrue(entry['has_next'])

    # ------------------------------------------------------------------
    # Visibility: the cache must never outrank a moderation decision.
    # ------------------------------------------------------------------
    def _assert_hidden_after_warming(self, status):
        self.client.get('/')                     # populate the cache
        target = self.newest_first[0]
        target.status = status
        target.save(update_fields=['status'])
        warm = self.client.get('/').content.decode()
        self.assertEqual(len(card_titles(warm)), PAGE_SIZE - 1,
                         f'a {status} vibe was still rendered from the cache')
        self.assertNotIn(target.title, warm,
                         f'a {status} vibe was still visible to anonymous visitors')

    def test_a_quarantined_vibe_leaves_the_cached_feed(self):
        self._assert_hidden_after_warming('quarantined')

    def test_a_removed_vibe_leaves_the_cached_feed(self):
        self._assert_hidden_after_warming('removed')

    def test_an_unpublished_vibe_leaves_the_cached_feed(self):
        self._assert_hidden_after_warming('pending')

    # ------------------------------------------------------------------
    # Everyone and everything else must be untouched.
    # ------------------------------------------------------------------
    def test_signed_in_users_never_use_the_cache(self):
        user = make_user('cache-viewer')
        self.client.force_login(user)
        cold = self.client.get('/?page=2').content.decode()
        warm = self.client.get('/?page=2').content.decode()
        self.assertEqual(card_titles(cold), self.expected(2))
        self.assertEqual(card_titles(warm), self.expected(2))
        self.assertIn('Page 2 of 3', warm)
        self.assertIsNone(cache.get('feed:anon:page:2:sort:newest:v3'))

    def test_filtered_feeds_never_use_the_cache(self):
        for url in ('/?page=2&q=vibe', '/?page=2&category=' + self.cat.slug,
                    '/?page=2&trust=verified', '/?page=2&ai=1',
                    '/?page=2&tech=python', '/?page=2&sort=trending'):
            cache.clear()
            cold = self.client.get(url).content.decode()
            warm = self.client.get(url).content.decode()
            self.assertEqual(card_titles(cold), card_titles(warm), url)

    def test_pages_after_three_are_never_cached(self):
        self.client.get('/?page=4')
        self.assertIsNone(cache.get('feed:anon:page:4:sort:newest:v3'))

    def test_a_bare_page_parameter_does_not_create_a_second_key(self):
        self.client.get('/?page=')
        self.assertEqual(pager_text(self.client.get('/?page=').content.decode()), 'Page 1')
        self.assertIsNone(cache.get('feed:anon:page::sort:newest:v3'))
        self.assertIsNotNone(cache.get('feed:anon:page:1:sort:newest:v3'))

    def test_an_unknown_sort_does_not_mint_its_own_cache_key(self):
        self.client.get('/?page=1&sort=not-a-sort')
        self.assertIsNone(cache.get('feed:anon:page:1:sort:not-a-sort:v3'))
        self.assertIsNotNone(cache.get('feed:anon:page:1:sort:newest:v3'))

    def test_an_empty_feed_is_not_cached_into_a_broken_page(self):
        AppProject.objects.all().update(status='pending')
        cache.clear()
        cold = self.client.get('/').content.decode()
        warm = self.client.get('/').content.decode()
        self.assertEqual(card_titles(cold), [])
        self.assertEqual(card_titles(warm), [])

    # ------------------------------------------------------------------
    # The point of the exercise: a hit must cost less than a miss.
    # ------------------------------------------------------------------
    def test_a_cache_hit_costs_fewer_queries_than_a_miss(self):
        cache.clear()
        cold_q, cold = self.queries('/')
        warm_q, warm = self.queries('/')
        self.assertEqual(card_titles(cold), card_titles(warm))
        self.assertLess(warm_q, cold_q,
                        f'the cache is not paying for itself ({cold_q} -> {warm_q})')

    def test_populating_the_cache_issues_no_extra_project_query(self):
        """Writing the entry must reuse the page we already rendered.

        Deriving the IDs from the queryset instead (`values_list('id')[:13]`)
        inherits the remix_count annotation and adds a second LEFT JOIN +
        GROUP BY — roughly 10x the cost of the COUNT(*) it saves.
        """
        cache.clear()
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/')
        cold = [q['sql'] for q in ctx.captured_queries]
        id_only = [s for s in cold
                   if re.search(r'SELECT\s+"?gallery_appproject"?\."id"\s+AS\s+"?id"?', s)]
        self.assertEqual(
            id_only, [],
            'populating the cache ran a separate id-only query:\n'
            + '\n'.join(s[:160] for s in id_only),
        )


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-feed-cache-tests')
class CachedFeedPageContractTests(TestCase):
    """CachedFeedPage must stand in for a Django Page without surprises."""

    def make_page(self, objects, number, has_next):
        from gallery.views import CachedFeedPage
        return CachedFeedPage(objects, number, has_next)

    def test_it_iterates(self):
        page = self.make_page(['a', 'b', 'c'], 1, False)
        self.assertEqual(list(page), ['a', 'b', 'c'])

    def test_it_supports_len_and_indexing(self):
        page = self.make_page(['a', 'b', 'c'], 1, False)
        self.assertEqual(len(page), 3)
        self.assertEqual(page[0], 'a')
        self.assertEqual(page[-1], 'c')
        self.assertIn('b', page)

    def test_an_empty_page_is_falsy_so_the_template_empty_clause_runs(self):
        page = self.make_page([], 1, False)
        self.assertEqual(len(page), 0)
        self.assertFalse(page)

    def test_pagination_flags(self):
        first = self.make_page([1], 1, True)
        self.assertTrue(first.has_next)
        self.assertFalse(first.has_previous)
        self.assertTrue(first.has_other_pages)
        self.assertEqual(first.next_page_number(), 2)

        middle = self.make_page([1], 3, True)
        self.assertEqual(middle.previous_page_number(), 2)

        last = self.make_page([1], 5, False)
        self.assertFalse(last.has_next)
        self.assertTrue(last.has_previous)
        # Other pages exist — you just can only go backwards from here.
        self.assertTrue(last.has_other_pages)

        only = self.make_page([1], 1, False)
        self.assertFalse(only.has_next)
        self.assertFalse(only.has_previous)
        self.assertFalse(only.has_other_pages)

    def test_missing_pages_raise_a_pagination_error_not_404(self):
        from django.core.paginator import InvalidPage
        page = self.make_page([1], 1, False)
        with self.assertRaises(InvalidPage):
            page.next_page_number()
        with self.assertRaises(InvalidPage):
            page.previous_page_number()

    def test_num_pages_is_none_because_counting_would_defeat_the_point(self):
        self.assertIsNone(self.make_page([1], 1, True).num_pages)

    def test_the_pager_renders_without_a_total(self):
        """Mirrors templates/gallery/feed.html: Page N, no dangling 'of'."""
        from django.template import Context, Template
        html = Template(
            'Page {{ page.number }}'
            '{% if page.paginator.num_pages %} of {{ page.paginator.num_pages }}{% endif %}'
        ).render(Context({'page': self.make_page([1], 2, True)}))
        self.assertEqual(html, 'Page 2')
