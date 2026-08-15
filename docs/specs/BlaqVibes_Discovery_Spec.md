# Program kinds, honest previews, and taste-ranked discovery

**Problem.** BlaqVibes accepts every kind of program, but only pasted
HTML/CSS/JS snippets can actually run in the sandboxed iframe. Everything
else — games, APIs, mobile apps, notebooks, CLI tools — was published with
the same "Preview files" chrome and ranked in one undifferentiated firehose.
With uploads arriving continuously the grid becomes unusable: nobody can
tell what a thing *is*, whether they can try it, or find the kind they care
about.

**Shape of the fix.** Three layers, each usable without the one above it:

| Layer | Module | Works without |
| --- | --- | --- |
| Taxonomy | `gallery/taxonomy.py` | anything |
| Heuristic classification | `gallery/kind_detect.py` | network, API key, money |
| LLM lift (selective) | `gallery/classify.py` | — degrades to heuristic |
| Global ranking | `gallery/interest.py` | any user signal |
| Personal ranking | `gallery/taste.py` | — degrades to global |

---

## 1. Taxonomy (`gallery/taxonomy.py`)

14 fixed kinds. Every producer funnels through `coerce_kind()`.

**5 Whys — why a fixed table and not a Category row or a free tag?**

1. `Category` is runtime-editable curation; an affinity row saying a user
   likes `game` must still mean `game` after a rename.
2. Free tags are user input — `game`, `Games`, `gaem`, `#unity` become four
   buckets and the learner never accumulates enough signal in any of them.
3. An LLM returning an off-taxonomy string would create a kind nothing can
   filter, rank, or learn from. `coerce_kind()` is the gate; a test asserts
   every signal table only emits taxonomy values.
4. Each kind declares its own `preview` capability, because "publish
   everything, be honest about what can't run" has to be data — otherwise
   every surface re-invents the rule and they drift.
5. 14 buckets, not 60: every extra kind splits the signal. Three opened
   games is an obvious preference at 14 buckets and noise at 60.

`preview_mode_for(kind, has_html, has_zip)` decides capability **per upload**,
not per kind — a game can arrive as a Unity ZIP with nothing runnable. A ZIP
never becomes `snippet`: serving user files from our origin is the XSS hole
the preview-token design exists to avoid.

## 2. Heuristic classification (`gallery/kind_detect.py`)

Weighted signals over shallow file paths (root + one folder, capped at 400),
directory names, extensions, README/title/stack keywords and language stats.
Weight scale: 5 = near-proof (`ProjectSettings/`, `.aab`), 1 = weak.

- **Why weighted, not first-match-wins like `artifact_detect`?** That module
  answers "which one deploy guide?", where specificity ordering suffices.
  Here Unity and React both ship a `package.json`; only the *balance* of
  evidence separates them.
- **Why a confidence score?** It is what makes the LLM stage selective.
- **Why keep evidence strings?** A creator who disagrees with the badge needs
  to see why, and a moderator triaging a mislabel needs the same.
- **Shape beats prose.** A pasted browser snippet cannot *be* a backend,
  mobile app, or desktop program however much its README says "backend" —
  non-`web_native` kinds are damped to 0.25 for snippet-only uploads. This
  is a real bug found while classifying the demo catalog ("Waitlist Minimal"
  was labelled `api_backend` from its marketing copy) and is regression-tested.

## 3. Selective LLM (`gallery/classify.py`)

Precedence: **creator pick → LLM → heuristic**.

**5 Whys — why is the LLM selective rather than always-on?**

1. One call per upload makes publish throughput equal to a vendor's rate
   limit and the cost linear in spam.
2. But the heuristic is genuinely lost on "Mzansi Runner — my side thing"
   with six `.js` files; those rows deserve a second opinion.
3. Gating on `confidence < 0.55` spends the budget where it changes the answer.
4. A **token bucket** (`KIND_LLM_CALLS_PER_MINUTE`, default 30) sits on top:
   confidence is per-upload, so a wave of a thousand identical low-confidence
   ZIPs would still pass the gate a thousand times. The bucket makes the worst
   case a constant per minute; rows that miss it keep their heuristic kind.
5. `coerce_kind` funnels the answer, so the LLM can never invent a bucket.

A broken cache fails **closed** (no LLM), never open. `KIND_LLM_CALLS_PER_MINUTE=0`
disables the LLM entirely. With no API key nothing is called and nothing breaks.

## 4. Global interest (`gallery/interest.py`)

`appeal_score` ∈ [0, 100] = (45·engagement + 35·quality + 10·runnable +
10·LLM appeal) × freshness, stored and indexed.

**5 Whys — why a stored batch score instead of `ORDER BY stars`?**

1. Stars never decay, so a handful of old vibes own page one permanently —
   nothing new is seen, so nothing new can earn stars. A closed loop.
2. `ORDER BY created_at` is the opposite failure: a polished game buried by
   fifty half-finished uploads within a minute.
3. Blending engagement + quality + freshness decay lets a new good upload
   out-rank an old good upload **without anyone voting** — the quality
   component is what solves cold start, since a new vibe has zero traffic
   by definition.
4. Stored, not annotated: an annotation recomputes logs and date arithmetic
   per row per page load and cannot be indexed.
5. Batch, not per-interaction: interactions are the highest-volume events on
   the site, and making each one write to a ranked, indexed column turns the
   hot path into index churn. A few minutes of staleness is invisible.

Recomputed by `refresh_appeal_scores` (Celery beat, every 10 min, queue
`rank` so it never sits in front of a scan), oldest-scored first with
`bulk_update` so every row is eventually refreshed no matter how many arrive.

## 5. Taste learning (`gallery/taste.py`, `KindAffinity`)

One row per (user, kind) — at most 14 per user, forever. Exponential decay,
30-day half-life, applied lazily on read.

**5 Whys — why a rolled-up table instead of ranking from raw events?**

1. Aggregating VibeView/Star/Trade at feed time means a per-user scan of the
   whole event history on the hot read path.
2. Caching that aggregate leaves the cold case unchanged; a materialised row
   has no cold case.
3. Per-kind, not per-project embeddings: 14 buckets is the smallest thing
   that can express "push games to the front", which is the actual request.
4. Decayed, not counted: a user who played games in March and now ships APIs
   should see APIs.
5. Lazy decay means no periodic job over every user, and a dormant user's row
   is correct the moment they return.

**Write path** is one UPDATE, never blocks, swallows every exception (a
download must not fail because a preference counter deadlocked), dedupes
repeat views in cache, and ignores views of your own vibes.

Weights: `trade` 8 > `publish` 6 > `fork`/`download` 5 > `star`/`save`/`comment` 3
> `preview` 2 > `view` 1. Unstarring subtracts nothing — a subtractable signal
is a griefing tool against your own recommendations.

**Read path** — `personalized_order()` inlines the ≤14 affinities as a SQL
`CASE`, so ordering happens in the database before pagination, with **no join
and a constant query count**. Boost is capped at `MAX_AFFINITY_BOOST = 50`
(half the appeal scale): a favourite kind beats anything in the better half
of the catalog and still loses to something outstanding in a kind they ignore.
Below 2 events there is no reordering at all — reordering on noise is worse
than not reordering.

## Feed rules

| Situation | Behaviour |
| --- | --- |
| Anonymous / new account | Global `appeal_score` order |
| Signed-in with ≥2 events | `For you` is the **default** sort |
| `?sort=newest` etc. | Never silently personalised — sort is a promise |
| `?program=game` | A filter is an instruction; it always beats the guess |
| `?runnable=1` | Only vibes that really can run (`preview_mode` + real HTML) |
| Text search | Relevance first, taste as tie-breaker |

## Honesty

- `can_run_preview` requires `preview_mode == 'snippet'` **and** real HTML —
  a mode alone can never fake a preview.
- Unrunnable vibes get an explicit "No live preview for a *{kind}*" panel and
  a `📁 Files only` badge instead of fake preview chrome.
- Owners see a `why?` chip listing the classification evidence, and can
  override the kind in Edit.

## Operations

```bash
# after editing the signal tables in kind_detect.py
python manage.py reclassify_vibes                 # heuristic, free
python manage.py reclassify_vibes --llm           # costs money, opt-in
python manage.py reclassify_vibes --force         # also overwrite creator picks
```

Env: `KIND_LLM_CALLS_PER_MINUTE` (0 disables), `KIND_LLM_CONFIDENCE_FLOOR`,
`APPEAL_BATCH_LIMIT`.

## Measured

5,000-vibe catalog, SQLite, warm:

| Path | Time | Queries |
| --- | --- | --- |
| Anonymous global feed | 16 ms | 5 |
| Personalised feed | 27 ms | 13 |
| Kind filter | 24 ms | 13 |
| Personalised, all 14 affinities | 33 ms | 13 (no join added) |
| Background rescore | 2,168 rows/s | bulk |

Query count is flat in both catalog size and affinity count.

## Tests

73 new tests in `gallery/tests.py` (211 total, all passing) covering the
taxonomy contract, detector accuracy per kind, the taxonomy-escape guard,
creator override, LLM coercion and budget exhaustion, cold start, freshness
turnover, boost bounding, view dedupe, self-view exclusion, decay, SQL-side
ordering, every feed rule above, honest preview rendering, the API contract,
and failure isolation (a broken recorder must not break a paid download).
