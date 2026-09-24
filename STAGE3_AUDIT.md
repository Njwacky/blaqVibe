# Stage 3 audit — anonymous feed pagination ("keep cached feed pager free of COUNT")

> ## ✅ RESOLVED — read this first
>
> Every finding below was reproduced and then **fixed on the branch that accompanies this
> document** (`arena/01a0cada-blaqvibe`). The audit is kept verbatim as the engineering record;
> it describes the state of `master` at commit `7496c949`, **which is now broken and superseded**.
>
> What the fix changed, and what it verified:
>
> | Bug | Fix |
> |---|---|
> | **B1** blank feed on every cache hit | `CachedFeedPage` now subclasses `collections.abc.Sequence` |
> | **B2** pages 2/3 served page 1's rows | IDs come from the already-materialised `page.object_list`, so they are page-correct by construction |
> | **B3** cached path skipped `status='published'` | The cached queryset filters on `status='published'` |
> | **B4** extra ~10× query to populate the cache | The cache entry is built from data already in hand — **zero extra queries** (cold 32 → 31) |
> | **B5/B6/B7/B9** | Short pages, duplicate `?page=` key, unvalidated `sort`, `Http404` → `EmptyPage` |
>
> Measured after the fix: warm anonymous page 1 = **8 queries, 1 `COUNT(*)`, 12 cards**,
> pages 1/2/3 all correct; a vibe removed after the cache warms **leaves the grid**.
> Full suite: **1429 tests, 30 failures — zero new failures vs the pre-Stage-3 baseline**
> (baseline 32, Stage 3 30 but two of those passed only because the feed was blank).
>
> **Not addressed here (deliberately):** the dominant feed cost, `annotate(remix_count=…)`
> (162 ms vs 2.9 ms without it at 8 007 rows). That is the next stage — see §7 items 7–12.

**Scope:** commits `a67c4054` (`perf: remove feed COUNT on cached anonymous pages`) and
`7496c949` (`perf: keep cached feed pager free of COUNT`) on `master`.
**Method:** independent inspection + an instrumented Django 5.2.17 harness running the real
`gallery.views.feed` through `django.test.Client` against a scratch SQLite database.
**No repository code was modified.** `git status` is clean.

> ### Headline
> **Stage 3 is not shippable.** On every anonymous cache hit it renders an **empty feed grid**.
> Once the page object is made iterable, cached pages **2 and 3 serve page 1's content**.
> And the optimisation it lands removes a sub-millisecond `COUNT(*)` while **adding** a query
> roughly 11× more expensive on the cold path. It does not address the reported ~5 s load.

---

## 0. Verification caveat (read this before quoting numbers)

All timings below were taken on **SQLite** in the sandbox. No Postgres server, no Docker, and no
Supabase instance was available, so **production performance has NOT been empirically verified.**
The *query counts, query shapes, and correctness findings are engine-independent*. The *relative*
timings (annotation vs no annotation, ~50×) are structural and will hold on Postgres; the absolute
milliseconds will not. Every timing claim below is labelled.

---

## 1. What is correct

| # | Finding | Evidence |
|---|---|---|
| C1 | **The diff is exactly the intended change and nothing else.** `views.py` +56/−5, `feed.html` +1/−1. Both staged hunks are the `CachedFeedPage` class, the cached branch, the cache-population line, and the pager span. I diffed the full files against `00c172fd` (parent of `a67c4054`) — zero collateral edits. | `diff -u` of both files |
| C2 | **The template change is correct and safe.** `{% if page.paginator.num_pages %}` is a no-op for a real `Paginator` (`num_pages` is an int ≥ 1, always truthy) and suppresses `of None` on the cached path. It is the right fix for the symptom it targets. | `templates/gallery/feed.html:78` |
| C3 | **The 13th sentinel correctly determines `has_next` for page 1.** Verified at every boundary: n=12 → 12 ids → `has_next=False`; n=13 → 13 ids → `True`; n=24 → `True`; n=25 → `True`. Matches Django's Paginator on the cold path in all four cases. | harness, boundary sweep |
| C4 | **The `Case/When` ordering is correct SQL.** `ORDER BY CASE WHEN id = 5 THEN 0 … ELSE NULL END ASC`. All 12 rows match (`pk__in` is exactly those ids), so no NULL ordering ambiguity; and Postgres sorts NULLS LAST on ASC anyway. Ordering is faithfully preserved. | generated SQL inspection |
| C5 | **The cached branch genuinely drops the `Paginator` COUNT(*).** Warm anonymous page 1 goes from 2 `COUNT(*)` to 1. The remaining one is `today_challenge()`, not the paginator. | query capture, before/after |
| C6 | **The bypass conditions are right.** Anonymous + unfiltered only. I exercised 16 URLs — `q`, `category`, `kind`, `program`, `runnable`, `trust`, `ai`, `tech`, `following`, `sort=trending|stars|foryou`, `page=4`, `page=abc`, `page=0`, `page=-1`, `page=99999` — all bypass the cache and render correctly. Authenticated traffic never reads or writes the anon key. | harness §F, §G |
| C7 | **The fallback to Django's `Paginator` is well-formed** — it is inside `try/except` and re-derives from the full queryset. It just doesn't cover the failure that actually occurs (see B1). | `views.py:310-312` |
| C8 | **It fixes a real pre-Stage-3 defect.** The old cached path fed 12 ids through `Paginator(..., 12)`, so every cached page reported **`Page 1 of 1`**, `number=1`, `has_next=False`, `has_previous=False` — the **Next/Prev links vanished on cached pages 2 and 3**, and page 1 lost Next even with 751 real pages. The sentinel design is the right shape for fixing that. | verified with `Paginator` directly |
| C9 | **`today_loop` and `grid_ids` remain safe** — both use `page.object_list`, which `CachedFeedPage` exposes as a real list. | `today_tags.py:20`, `views.py:345` |

---

## 2. What is wrong

### B1 — P0 · `CachedFeedPage` is not iterable → **every anonymous cache hit renders an empty feed**

`CachedFeedPage` (`views.py:67-105`) defines `object_list` but **not `__iter__`, `__len__`, or
`__getitem__`**. Django's `Page` subclasses `collections.abc.Sequence`, which supplies them.

`templates/gallery/feed.html:41` does `{% for p in page %}`. Django 5.2's `ForNode.render`:

```python
values = self.sequence.resolve(context, ignore_failures=True)
if values is None: values = []
if not hasattr(values, "__len__"):
    values = list(values)          # ← TypeError on CachedFeedPage
```

The `TypeError` propagates out of `render()` into the view's **outer** `except Exception`
(`views.py:385`), which logs `"feed crush silent"` and renders the emergency fallback context —
`Paginator(AppProject.objects.none(), 12).get_page(1)`, i.e. **zero cards, "Page 1 of 1", no
Prev/Next**, HTTP **200**.

Measured, on every anonymous cache hit, at every catalogue size (0, 1, 11, 12, 13, 24, 25, 3007,
8007) and every sort (`newest`, `trending`, `stars`):

```
WARM: cards=0  pager="Page 1 of 1"  next=False  prev=False
```

**This is the worst possible failure mode:** no 500, no user-visible error, Sentry gets one
`logger.exception` line, and the feed looks "empty because nobody has published".

### B2 — P0 · Cached pages 2 and 3 serve **page 1's content**

`views.py:320` populates the cache with:

```python
ids = list(projects.values_list('id', flat=True)[:13])
```

`projects` is the **unsliced, un-offset** queryset. The cache key however **is per page**
(`views.py:290`). So all three page keys receive the first 13 rows of the whole catalogue:

```
feed:anon:page:1:sort:newest:v2 = {'ids': [32,31,30,29,28,27,26,25,24,23,22,21,20]}
feed:anon:page:2:sort:newest:v2 = {'ids': [32,31,30,29,28,27,26,25,24,23,22,21,20]}
feed:anon:page:3:sort:newest:v2 = {'ids': [32,31,30,29,28,27,26,25,24,23,22,21,20]}
```

With `__iter__`/`__len__` added **in memory only** (repo untouched) to expose the logic behind the
crash, 25 projects:

| Request | Cold (correct) | Warm / cached |
|---|---|---|
| page 1 | Vibe 024…013, "Page 1 of 3", Next ✓ | Vibe 024…013, "Page 1", Next ✓ **correct** |
| page 2 | Vibe 012…001, "Page 2 of 3" | **Vibe 024…013**, "Page 2", Prev + Next ✗ |
| page 3 | Vibe 000, "Page 3 of 3" | **Vibe 024…013**, "Page 3", Prev + Next ✗ |

A signed-out visitor clicking Next lands on page 2, sees the same 12 cards again, clicks Next
again, and sees them a third time. It is also **non-deterministic in production**: the first
request after each 30 s TTL expiry is correct, and every request inside the window is wrong.

### B3 — P1 · The cached path drops the `status='published'` filter

`views.py:307`:

```python
projects_filtered = AppProject.objects.filter(pk__in=page_ids).select_related(...)...
```

No `status` filter. Every other feed path starts from
`AppProject.objects.filter(status='published')`. Verified: 3 on-page projects flipped to
`removed` (and separately the sentinel) **are still rendered** from the cache.

*Inherited from Stage 2* — the old code had the same hole — but Stage 3 currently **masks** it
with the blank page rather than fixing it. Consequence once B1 is fixed: a vibe quarantined for
malware/secrets, or removed by its owner, **stays visible to anonymous visitors for up to 30 s**.

### B4 — P1 · The new cache-population query is ~11× more expensive than the COUNT it replaces

`projects` carries `annotate(remix_count=Count('forks', ...))`, so the "cheap 13 ids" query
inherits a **LEFT OUTER JOIN + `GROUP BY` over every selected column + `ORDER BY` + `LIMIT 13`.**
Measured on SQLite, 8 007 rows / 3 000 forks:

| Query | Median |
|---|---|
| `Paginator` `COUNT(*)` — **the query Stage 3 removes** | **2.68 ms** |
| `values_list('id')[:13]` **with** the annotation — **the new query** | **29.34 ms** |
| `values_list('id')[:13]` **without** the annotation | 0.25 ms |

End-to-end query counts, identical dataset, current vs pre-Stage-3:

| | Baseline | Stage 3 |
|---|---|---|
| Cold anonymous page 1 | 31 queries | **32 queries** |
| Cold anonymous page 2 | 24 queries | **25 queries** |
| Warm anonymous page 1 | 9 queries, 2 `COUNT(*)` | 9 queries, **1** `COUNT(*)`, 0 cards |

So: **−1 query on the warm path, +1 expensive query on the cold path.**

### B5 — P2 · Deleted rows leave a short page and a stale `has_next`

Hard-deleting 3 of the 12 cached ids gives **9 cards instead of 12**, with `has_next` still `True`
because the sentinel id is cached separately from the rows. Grid gets holes until the TTL expires.

### B6 — P2 · `?page=` (empty) creates a second cache key for identical content

`page_num` is `''`, which is in the whitelist but produces `feed:anon:page::sort:newest:v2` — a
distinct key holding the same 13 ids. Doubles cold-path work for those URLs.

### B7 — P2 · `sort` is unvalidated but is used in the cache key

`?sort=<anything>` produces a distinct cache key and its own cold miss. `program` is whitelisted by
`coerce_program_kind_filter`; `sort` is not. Unbounded key space from a single GET parameter.

### B8 — P2 · No invalidation, no stampede guard, 30 s TTL

Nothing invalidates `feed:anon:*` on publish, unpublish, quarantine or delete (grep for
`feed:anon` returns only the one write site). Combined with B3, that is a 30 s moderation window.

### B9 — P2 · `next_page_number()` / `previous_page_number()` raise `Http404`, not `InvalidPage`

Django's `Page` raises `InvalidPage` (a plain `Exception`). Any unguarded call becomes a real 404
response rather than being catchable as a pagination error. Currently safe only because the
template guards both with `{% if %}`.

### B10 — P2 · `num_pages = None` is a UX regression on cached pages

Intentional, but the user now sees `Page 1` where an uncached visitor sees `Page 1 of 751`. Fine
if deliberate; worth a product decision rather than a side effect.

---

## 3. Edge cases (all measured)

| n | page 1 cold | page 1 warm | page 2 cold | page 2 warm |
|---|---|---|---|---|
| 0 | empty-state card | **0 cards** | empty-state card | **0 cards** |
| 1 | 1 card, "Page 1 of 1" | **0 cards** | 1 card | **0 cards ("Page 2")** |
| 11 | 11 cards, no Next ✓ | **0 cards** | 11 cards | **0 cards** |
| 12 | 12 cards, no Next ✓ | **0 cards** | 12 cards | **0 cards ("Page 2")** |
| 13 | 12 cards, "Page 1 of 2", Next ✓ | **0 cards** | 1 card, "Page 2 of 2" | **page 1's 12 cards** |
| 24 | 12 cards, Next ✓ | **0 cards** | 12 cards, no Next | **page 1's 12 cards** |
| 25 | 12 cards, "Page 1 of 3" | **0 cards** | 12 cards, "Page 2 of 3" | **page 1's 12 cards** |

Cross-cutting: empty feed ✓ (falls through to `Paginator`, `cached_ids` is falsy) · anonymous ✓ ·
authenticated ✓ unaffected · search/category/kind/program/runnable/trust/ai/tech/following ✓ all
bypass · `following=1` anonymous → 302 ✓ · `page=abc/0/-1/99999` → clamped by `get_page`, bypass
cache ✓ · corrupted cache value → `isinstance` guard ✓.

---

## 4. Performance problems still remaining (and where the ~5 s actually lives)

Anonymous page 1, 8 007 projects / 3 000 forks, SQLite, media of 7 runs:

| Operation | Median | Note |
|---|---|---|
| Feed page query **with** `remix_count` annotation | **162 ms** | cold path |
| Same page query **without** the annotation | **2.91 ms** | **56× faster** |
| `Paginator` `COUNT(*)` | 2.68 ms | **1.6 % of the page query** |
| Remix counts for 12 ids as one separate aggregate | 3.71 ms | |
| **Page rows + remix counts, no annotation, no COUNT** | **6.32 ms** | **26× faster than today's cold path** |
| `values_list('id')[:13]` with annotation (Stage 3's new query) | 29.34 ms | |
| `projects[:1].exists()` | 0.68 ms | runs on **every** request |

**The single dominant cost of the feed is `annotate(remix_count=Count('forks', ...))`.** It forces
PostgreSQL to materialise a `LEFT OUTER JOIN` across the whole catalogue and `GROUP BY` every
selected column before it can sort and take 12 rows. `COUNT(*)` is 1.6 % of it. Every other feed
counter (`stars`, `clones`, `copies`, `views`, `review_count`, `file_count`) is already a
denormalised column — remix count is the one that isn't.

Remaining per-request work on a warm anonymous hit (8 queries, once B1 is fixed):

1. `SiteSettings` — one path is cached, one is not
2. `projects[:1].exists()` — **full join + GROUP BY on every request**, only used to decide whether
   to auto-seed demo content under `SEED_DEMO` (off in production)
3. the 12-row page query
4. tag prefetch
5. trending rail fallback
6–8. `today_challenge()` — **3 uncached queries on every request** (`ensure_daily_challenge`
select, submissions `COUNT`, leaderboard select)

And the cold path issues **32 sequential queries**. Against Supabase over TLS, ~32 serial round
trips is plausibly 1–2 s of pure latency before a single row is rendered.

**Conclusion: the ~5 s is query count × network round trips plus one very expensive annotated
query. Stage 3 addresses neither.**

---

## 5. Does Stage 3 achieve its stated goal?

> *"reduce unnecessary database work on cached anonymous feed pages, especially the expensive
> `COUNT(*)` query generated by Django's `Paginator`."*

| Claim | Verdict |
|---|---|
| Cached pages avoid `COUNT(*)` | **Technically true** — 2 → 1 `COUNT(*)` on the warm path |
| The `COUNT(*)` was "expensive" | **False.** On the cached path it counted
`AppProject.objects.filter(pk__in=<12 ids>)` — 12 primary keys, sub-millisecond, index-only |
| Preserves correct Next-page behaviour | **False.** Broken by B1 (blank) and B2 (wrong page) |
| Reduces database work overall | **False.** −1 trivial query on warm, +1 expensive query on cold |
| Addresses the ~5 s feed | **False.** `COUNT(*)` is 1.6 % of the page query |

**It optimised the one query that did not need optimising, and in doing so broke the page it was
meant to speed up.**

---

## 6. Test-suite findings (informational)

I ran the full suite (`gallery` + `users`, 1404 tests) on both the current code and a pristine
checkout of `00c172fd`, with identical settings:

| | Baseline | Stage 3 |
|---|---|---|
| Failures + errors | 32 | 30 |

30 are shared and environment-related (social-auth config, footer contacts, `LocalDevPosture`
under `DEBUG=1`). Two differ, and the direction is alarming:

```
test_removed_vibe_leaves_feed (gallery.tests.DeleteLifecycleTests)
test_removed_vibe_is_gone_from_feed_api_and_sitemap (Scenario4_OldUrlsAfterUnpublish)
```

Both **fail on the baseline and pass on Stage 3** — because Stage 3 renders a **blank** feed, and
`assertNotIn(title, body)` is satisfied by a page with nothing on it. Both pass in isolation on
both versions; they only diverge under cross-test `LocMemCache` leakage.

Two structural testing gaps this exposes:

1. **No test makes a second anonymous request.** Every feed test is a cache miss, so the entire
   cached path is untested. Ten reproduction tests I wrote outside the repo fail 7/10 on the
   current code.
2. **The suite is order-dependent.** `LocMemCache` is never cleared between tests, so
   `feed:anon:page:1:sort:newest:v2` leaks across test cases and modules.

---

## 7. Recommendations for the next stage

### Stage 4a — make the current design correct (do this first)

1. **Make `CachedFeedPage` a proper sequence.** Subclass `collections.abc.Sequence` and add
   `__len__`, `__iter__`, `__getitem__` — or, far simpler and safer, **don't invent a page class at
   all**: build the ids, then wrap in a real `Paginator` with `num_pages` spoofed. Fixes B1.
2. **Cache page 1 only** (`page_num == '1'`), or compute the offset properly —
   `projects.values_list('id', flat=True)[offset:offset+13]` with `offset = 12*(page-1)`. Page 1 is
   the page the CDN header targets and the page that gets the traffic. Fixes B2 and B6 in one move.
3. **Filter by status on the cached path**:
   `AppProject.objects.filter(pk__in=page_ids, status='published')`, and drop any id that comes back
   missing. Fixes B3 and B5.
4. **Populate the cache from a queryset without the annotation** —
   `AppProject.objects.filter(status='published').order_by(...)` mirroring the sort, or better, take
   `per_page+1` ids from the page query itself. 0.25 ms instead of 29.34 ms. Fixes B4.
5. **Whitelist `sort`** before it reaches a cache key (mirror `coerce_program_kind_filter`). Fixes B7.
6. **Raise `InvalidPage`, not `Http404`.** Fixes B9.

### Stage 4b — actually fix the 5 s (this is where the win is)

7. **Kill the `remix_count` annotation from the feed queryset.** Either:
   - compute it as a second, small aggregate over the 12 ids (measured **6.32 ms** total vs
     **162 ms** — **26×**), or
   - denormalise it into a column like the existing `stars` / `clones` / `copies` counters,
     maintained on fork publish/unpublish.

   This one change is worth more than every other item on this list combined.
8. **Replace `Paginator` with a fetch-`per_page+1` paginator for *all* pages**, anonymous and
   authenticated. That removes the full-table `COUNT(*)` from the cold path too — the query Stage 3
   claimed to be removing but did not — at the cost of never showing "Page X of N".
9. **Guard `projects[:1].exists()` behind `if settings.SEED_DEMO:`** so production stops paying for a
   full join+group on every single request.
10. **Cache `today_challenge()`** (3 uncached queries/request) and confirm the second, uncached
    `SiteSettings` read.
11. **Invalidate `feed:anon:*` on publish / status change / delete** instead of relying on a bare 30 s TTL.
12. **Consider caching the rendered grid fragment, not just ids.** With the catalogue-ordered ids
    cached, a warm hit still pays 4 queries; a cached HTML fragment for page 1 would take the
    anonymous feed to ~1–2 queries.

### Process

13. **Add regression tests that hit the feed twice** as an anonymous user — content equality,
    Next/Prev presence, page 2/3 correctness, and "a quarantined vibe must not appear on a cached
    page *and the page must be non-empty*". The last clause is what would have caught B3 passing
    vacuously.
14. **Clear the cache between tests** (or use a per-test cache backend). The suite is currently
    order-dependent and hides exactly this class of bug.
15. **Narrow the view's outer `except Exception`.** It converted a `TypeError` into an empty feed
    with HTTP 200. Catching broadly around `render()` specifically is what turned a loud crash into
    a silent one.

---

### Appendix — artefacts

Reproduction harness and tests live outside the repo in `/tmp/audit/`
(`harness2.py`, `harness3.py`, `harness4.py`, `bench.py`, `bench2.py`, `audit_tests.py`,
`count_queries.py`) plus a pristine pre-Stage-3 checkout at `/tmp/baseline`. Nothing under
`/home/user/blaqVibe` was modified.
