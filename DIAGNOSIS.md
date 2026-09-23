# BlaqVibes diagnosis — 2026-09-23

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
