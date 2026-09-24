# BlaqVibes diagnosis — 2026-09-23

> ## ✅ Fix round 2 applied (item 3, the template/copy-drift cluster) — same day
>
> **Suite: 1435 tests / 5 failures → 1435 tests / 0 failures — OK.** The first fully
> green run since the performance stage. All three `scripts/ci.sh` hardening gates still
> pass. No test was deleted, skipped, or weakened — every fix below restores the
> pinned copy/behaviour in the product.
>
> | # | Failure | Root cause | Fix |
> |---|---|---|---|
> | 1 | `test_feed_links_creator_names_to_profiles` — `/` missing `/u/feedstar/` | The feed card rendered `@owner` as plain text. The card is one big `<a>`, and anchors cannot nest, so a byline link needs the card restructured. | `templates/gallery/feed.html`: card is now a `<div class="card vibe-card card--click">` with a stretched overlay link (`.vibe-card__go`) keeping the whole card one click target, and the byline is a real link via `{% url 'profile_view' p.owner.username %}` (named URL, can't drift). Hover/lift parity kept by extending the `a.card` rules in `blaqvibes.css` with `.card--click`; `.vibe-card__go` lives in `cards.css` next to the other `.vibe-card` rules (which already anticipated a byline link: `.vibe-card__meta a`). CSS `?v=` cache-busters bumped. |
> | 2 | `test_landing_leads_with_the_community_question` — two of four hero strings gone | Hero rewrite dropped the answer tagline and the explore CTA. | `templates/gallery/feed.html`: restored **"Your work belongs in the answer."** under the `<h1>` and **"Explore what's being built"** as the primary CTA anchor into `#grid` (replacing the drifted "Browse projects →" anchor that pointed at the same target). |
> | 3 | `test_feed_promises_render_and_link_the_standard` — "Read the standard →" gone | Label drifted to "Trust standard →" (target was already right: `{% url 'trust_legend' %}` *is* the `/trust/` route — same view, `gallery/urls.py`). | `templates/gallery/feed.html`: label restored to **"Read the standard →"**, still linking `/trust/`. The footer's "Trust standard" nav link is untouched. |
> | 4 | `test_upload_handoff_marks_welcome_seen_before_rendering_publish` — overlay re-rendered on `/publish/?welcome=1` | **Correction to the round-1 analysis:** the publish view never *lost* the handoff — `gallery/views.py` still calls `welcome.mark_seen(request.user)` on `?welcome=1`. The real bug: `mark_seen` writes with `Profile.objects.filter(...).update(...)`, which bypasses the ORM, so the in-memory `request.user.profile` this request already loaded stays `overlay_seen=False` and `base.html`'s gate (`not user.profile.overlay_seen`) re-includes the overlay a few lines later. The DB flag *was* set (the test's `refresh_from_db()` assertion passed; only the `assertNotContains` failed). | `users/welcome.py`: `mark_seen` now syncs the in-memory instance (`profile.overlay_seen = True`) after the `.update()`, with a comment explaining why. Idempotency and never-unset semantics unchanged — the whole `users.test_welcome_overlay` module is green. |
> | 5 | `test_no_ui_file_renders_an_emoji` — `welcome_overlay.html:42` rendered `⬆` (U+2B06) | Genuine rule violation in the one file the emoji sweep missed. | `templates/gallery/includes/welcome_overlay.html`: replaced the emoji with the sprite glyph `<svg class="bv-glyph" aria-hidden="true"><use href="#bv-ico-upload"></use></svg>` — already defined in the `base.html` sprite, so the companion "every referenced glyph exists" test stays green. |
>
> Also: `docs/STABILITY.md` §3.4 now documents the dirty-`.env` hazard from the
> environment notes below (leaks into `LocalDevPostureTests` subprocesses and
> `scripts/ci.sh` gate 2 → phantom failures; local `.env` for test runs should carry
> only `SECRET_KEY`).

> ## ✅ Fix round 1 applied (items 1 & 2) — same day
>
> **Suite: 1429 tests / 28 failures + 2 errors → 1435 tests / 5 failures.** All three CI
> hardening gates pass; the two posture probes boot again.
>
> What changed:
>
> | Fix | Files |
> |---|---|
> | Cache-isolated test runner: flushes the default cache after every test (`stopTest`), keeping caching fully working *inside* a test so the cached-feed tests still exercise warm hits. Only flushes a locmem/dummy backend — never Redis. | `blaqvibes/testing.py` (new), `TEST_RUNNER` in `blaqvibes/settings.py` |
> | Invalidation on write for the 10-minute footer-contacts cache, via `post_save`/`post_delete` so every writer is covered (operator editor, Django admin, queryset deletes). | `users/signals.py` |
> | Invalidation on save for the 120 s SiteSettings cache. | `users/models.py` |
> | Empty contact list is now an *empty footer* (only the Nolo section) as the spec test demands; the shipped-defaults fallback fires only when the contacts module itself fails. | `gallery/context_processors.py` |
> | `DATABASE_URL` raise rescoped: audits/inspections (`security_check`, `check`, `collectstatic`, `makemigrations`, …) and bare `python -c` imports boot on the SQLite placeholder; gunicorn/celery/runserver without a DB URL still refuse. | `blaqvibes/settings.py` (`_DB_AUDIT_COMMANDS`, `_inspecting_without_database`) |
> | Regression pins: cache isolation between tests, footer edit visible before the TTL, SiteSettings save drops its cache. (+6 tests) | `gallery/test_architecture.py`, `users/test_footer_contacts.py`, `users/test_site_settings.py` (new) |
>
> Corrections to the analysis below: the capability-search ERROR (`people[1]`) and
> `test_feed_links_creator_names_to_profiles` were **both** first misread — capability search
> caches too (`capability:search:*`), so its ERROR was cache poisoning and is now fixed; the
> feed-links failure is a *genuine template bug* (the card renders `@feedstar` as plain text,
> no `<a href="/u/feedstar/">`), confirmed by rendering the feed by hand.
>
> The 5 remaining failures are all the template/copy-drift cluster (item 3) + capability
> logic is fine — hero copy, "Read the standard →", the welcome-overlay handoff, the feed
> creator link, and the `⬆` emoji in `welcome_overlay.html:42`.

Full health check of the repo at `3da796a` (merge of PR #121, tip of `master`), run on a clean
Python 3.11 venv with `requirements.txt` installed as pinned.

## Verdict

| Area | Status |
|---|---|
| Dependencies install from pinned `requirements.txt` | ✅ clean |
| `manage.py check` | ✅ 0 issues |
| Migrations vs models (`makemigrations --check`) | ✅ no drift |
| `migrate` on fresh SQLite | ✅ all apply (minor: seeder warns "Seed ZIP missing `media/apps/zips/app.zip`") |
| CI gate 1 — hardened prod posture passes `security_check --strict` | ✅ |
| CI gate 2 — shipped defaults must be *refused* on a public host | ❌ **crashes instead of reporting findings** |
| CI gate 3 — `seed_demo` refused in prod posture | ✅ |
| `diagnose_email` command | ✅ works; correctly stops at first broken link (console backend / no `BREVO_API_KEY` in a bare sandbox) |
| Test suite | ⚠️ **1429 tests, 28 failures + 2 errors** — exactly the red baseline the repo already documents |

The suite total matches `STAGE3_AUDIT.md` line 21 verbatim:
> Full suite: **1429 tests, 30 failures — zero new failures vs the pre-Stage-3 baseline**

So: **nothing here is a new regression** — but ~22 of the 30 baseline failures share one root
cause that also hides a real production bug, and two failures (plus CI gate 2) expose a
settings/test contract break. Details below.

---

## Root cause A — performance caches poison the test run (≈22 of 30 failures)

The perf stage added long-TTL caches for data-driven UI (`gallery/performance.py`,
`gallery/context_processors.py`, `gallery/trending.py`). Django's test runner rolls back the DB
between tests but **never flushes the locmem cache**, so the first render that runs while the
fixture data is empty (or computed from un-overridden settings) caches that state and every
later assertion in the ~15-minute run reads the stale value.

**Proof:** the failing tests pass when run alone and fail in the suite/module:

- `users.test_social_auth.SocialButtonTests.test_all_three_buttons_when_configured` → **OK alone**, fails in suite
- `users.test_footer_contacts.FooterContactsAdminTests.test_admin_can_add_two_emails_a_whatsapp_number_and_x` → **OK alone**, fails in module (module run: 8F + 1E)

I also reproduced the footer pipeline by hand outside the test runner: rows save, normalise and
render correctly (`mailto:`, `https://x.com/...`, `https://wa.me/...` all appear on `/`).

Cached keys involved and what they break when stale:

| Cache key | TTL | Broken tests |
|---|---|---|
| `perf:footer_contacts:v1` | 600 s | 8 × `FooterContactsAdminTests` (+1 ERROR) — footer shows fallback defaults instead of saved rows |
| `perf:social_providers:v1` | 600 s | 3 × `SocialButtonTests`, `SocialConnectionManagementTests`, `test_login_shows_google_when_configured` — "Continue with Google/GitHub" missing |
| `ctx:nolo_backend` | 600 s | 2 × `NoloChatPageLabelTests` — wrong "Answers come from …" label (computed from env, ignores `override_settings`) |
| `ctx:unread:{pk}` | 30 s | `test_unread_count_context_processor_counts_only_unread` (0 ≠ 1) |
| `ctx:feedback_unread` | 60 s | `test_superadmin_sees_unread_count` — nav badge missing |
| `ctx:attention:{pk}` | 60 s | 2 × `AttentionViewTests` — account-menu link / `data-attention-banner` missing |
| `trending:scores:*`, `trending:fallback:*` | 120 s | 2 × `TrendingAndRemixTests` (`is_hot` False), `test_activity_summary_counts_only_real_rows` (stars 0 ≠ 1), `RemixStatsWindowTests` (2 ≠ 1) |
| `feed:anon:page:*` (Stage 3 cached feed) | until evicted | likely `test_feed_links_creator_names_to_profiles` (`/u/feedstar/` missing) — cached card ids from an earlier test's catalogue |

Note `cache_get_or_set()` treats a cached `[]` as a hit (`if hit is not None`), so one empty
read sticks for the full TTL. And these pks/keys **collide across tests** (each test's user can
be pk 1), so per-user counters leak between tests.

**This is not just a test problem.** The same missing invalidation means in production an
operator who edits footer contacts, social buttons, or answers the feedback inbox sees the old
state for up to **10 minutes** — there is no `cache.delete()` on save anywhere in
`users/admin_views.py`.

## Root cause B — fail-closed `DATABASE_URL` raise breaks the "explicit prod" posture (2 failures + CI gate 2)

`blaqvibes/settings.py:583` raises `RuntimeError: DATABASE_URL must be set in production`
whenever `LOCAL_DEV or TESTING` is false and no `DATABASE_URL` is set. Consequences:

- `LocalDevPostureTests.test_an_activated_virtualenv_alone_is_not_dev_mode` and
  `test_the_explicit_flag_beats_debug` spawn subprocesses that import settings with
  `DEBUG`-on/`DJANGO_LOCAL_DEV=0` (and, in the first case, nothing set) and assert the process
  *boots* — it now crashes at import instead.
- `scripts/ci.sh` **gate 2** runs `security_check` with `DEBUG=0 DJANGO_LOCAL_DEV=0` and no
  `DATABASE_URL` precisely to assert the shipped defaults are *reported* — settings can no
  longer be imported in that posture, so the gate fails with the traceback instead of the
  three expected findings. Gates 1 and 3 pass.

The hardening intent is right; the contract (what "bootable" means when `DEBUG=1` explicitly
asks for a public posture without a DB URL) needs deciding, and the test + gate updated to
match — or the raise relaxed for explicit-DEBUG boots.

## Root cause C — template/copy drift vs spec-tests (≈5 failures)

Strings the tests treat as product spec no longer exist in any template (grep across
`templates/` finds them only in test files):

- `test_landing_leads_with_the_community_question` — `'Your work belongs in the answer.'` absent from the landing page
- `test_feed_promises_render_and_link_the_standard` — `'Read the standard →'` absent from the feed
- `test_no_ui_file_renders_an_emoji` — genuine rule violation: `templates/gallery/includes/welcome_overlay.html:42` renders `⬆` (U+2B06); icons must come from the SVG sprite
- `test_upload_handoff_marks_welcome_seen_before_rendering_publish` — the upload→publish handoff no longer marks the welcome overlay seen (overlay renders when it must not)
- `CapabilitySearchTests.test_hardest_test_proof_beats_popularity_noise` (ERROR) — capability search returns fewer people than the fixture expects (not cache-related; scoring/query logic needs a look)

## Environment notes from this run

- The sandbox has no `.env`; the app correctly **fails closed** without `SECRET_KEY`. A local
  dev `.env` (gitignored, `SECRET_KEY` only) was created at the repo root to run the suite —
  delete or keep as you like.
- With a dirty `.env` (`DEBUG=1`, `EMAIL_BACKEND=console`) the suite shows 31 problems instead
  of 30 and `ci.sh` gate 2 fails noisily: `.env` values leak into subprocess-based tests
  (`LocalDevPostureTests`) and into the gates. Worth a line in `docs/STABILITY.md`.
- `diagnose_email` behaves exactly as documented: it stopped at "console backend / empty
  `BREVO_API_KEY`", which is the truth for a bare sandbox. On a real deploy it walks the chain
  to a live test send with `--to`.

---

## Suggested fix order

1. **Make the test runner cache-safe (fixes ~22 failures at once).** Options, pick one:
   - In `gallery/performance.py` / `context_processors.py` / `trending.py`, bypass or aggressively shorten TTLs when `settings.TESTING` (the flag already exists in settings).
   - Or force a flushed/dummy cache under `manage.py test` (e.g. `DJANGO_TEST=1` → `CACHES` locmem with `VERSION = os.getpid()` still collides — a `DummyCache`/per-test clear is cleaner).
   - Either way, **add invalidation on write** for `perf:footer_contacts:v1`, `perf:social_providers:v1`, `ctx:nolo_backend` in the admin save paths — that is the real production bug hiding under the red suite (operator edits stale for up to 10 min).
2. **Resolve the `DATABASE_URL` contract** (settings.py:583) and update both
   `LocalDevPostureTests` subprocess cases and `ci.sh` gate 2 to the decided behaviour.
3. **Reconcile copy drift**: either restore the missing strings (hero, "Read the standard →")
   or update the spec-tests; replace the `⬆` in `welcome_overlay.html:42` with the sprite icon;
   mark the welcome overlay seen in the upload→publish handoff.
4. **Investigate capability search scoring** for the `people[1]` IndexError.
5. Re-run `bash scripts/ci.sh` end-to-end — with 1–3 fixed the suite should reach its first
   fully green run since the perf stage landed.
