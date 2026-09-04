# BlaqVibes — Final Audit Report

**Branch:** `arena/01a06b19-blaqvibe`
**Baseline commit:** `c1bc732` (merge PR #56)
**Suite result:** **811 tests, 0 failures** (`python manage.py test --keepdb`)
**Env required:** `DJANGO_LOCAL_DEV=1 SECRET_KEY=...` for every `manage.py`/test command.

This is the consolidated output of the full **product + security + retention** pass.
Security items were prioritized P0–P1 before P2–P4 UX/retention, exactly as instructed.

---

## A. Security issues found

### A.1 Fixed in this pass

| # | Severity | Issue | Where it was a bug |
|---|----------|-------|--------------------|
| 1 | **HIGH** | Battle pages / history / voting leaked metadata (titles, owners, star counts) of a vibe that had gone `pending`/`quarantined`/`removed`. No visibility guard on any battle read. | `gallery/views_community.py` — `battle()`, `battle_history()`, `vote_battle()` |
| 2 | **HIGH** | `vote_battle`'s broad `except Exception` swallowed `Http404` from the visibility gate and returned a **302**, which (a) confirmed the battle existed and (b) hid the refusal (same anti-pattern PR pages had). | `gallery/views_community.py::vote_battle` |
| 3 | **MEDIUM** | New notification write endpoints did not exist (inbox only marked-all on open). The single-notification-mark and explicit mark-all endpoints were absent, so an owner couldn't clear one without touching the rest, and there was no clean JSON count API. | `gallery/views.py`, `gallery/urls.py` |
| 4 | **LOW** | `toggle_star` was a write endpoint with **no** `@login_required @require_POST` (unlike `toggle_bookmark`). Anonymous callers reached `toggle_project_star(AnonymousUser, ...)`; the NOT NULL FK error was swallowed as "you starred it". | `gallery/views.py::toggle_star` |
| 5 | **LOW** | `AppProject.trust` (the "passed the human+scanner gauntlet" signal the marketplace ranks on) was editable by a superuser in the Django admin, bypassing the pipeline-only writer rule. | `gallery/admin.py::AppProjectAdmin` |
| 6 | **INFO** | Hermetic rate-limit tests in two modules shared an identical locmem `LOCATION`, leaking cache state across run order and making `test_login_post_is_rate_limited_at_20_per_minute` flaky. | `gallery/test_security_regressions.py` |

### A.2 Re-audited this pass and confirmed SAFE (no action needed)

Audited the entire request surface against `user_can_see_project` / `user_can_download`:

- `battle_leaderboard` filters `status='published'` only; `daily.submissions`/`leaderboard` filter `published`; `challenge_detail` defaults to `published` (admin-only override otherwise).
- `post_comment`/`post_review` fetch `status='published'` and refuse on non-public.
- `download_zip` (published/removed/pending via `user_can_download`), `download_version` (gated by `user_can_see_project or user_can_download`), `scan_status` (`user_can_see_project`), `file_preview` (`status='published'`).
- `users/views.py`: `delete_account` is owner-scoped on `request.user` + username confirmation; `rename_username`, `edit_email`, `request_payout`, `tip_user`, `toggle_setting` are all `@login_required` + POST-only + rate-limited.
- `api_views.py`: list/detail are `published`-only; `scan_report` never serialized.

---

## B. Files changed

- `gallery/admin.py` — `AppProject.trust` now read-only in admin.
- `gallery/urls.py` — registered `/inbox/read-all/` and `/inbox/<int:notification_id>/read/`.
- `gallery/views.py` — added `notifications_mark_read`, `notifications_mark_all_read`; added `@login_required @require_POST` to `toggle_star`.
- `gallery/views_community.py` — added `_battle_visible()` helper; wired into `battle()`, `battle_history()`, `vote_battle()`; moved visibility gate + `get_object_or_404` before the broad `try`; hoisted `VibeBattle` to module scope; added `select_related` for owner/co-owner and a slightly larger fetch window before the visibility filter.
- `gallery/test_security_regressions.py` — cleared the locmem ratelimit cache at `setUp` in `AuthRateLimitRegressionTests` and `CspReportRateLimitRegressionTests` (deterministic counters).
- `gallery/test_retention_security.py` — **new** regression module (21 tests; see §C).

---

## C. Tests added

`gallery/test_retention_security.py`:

| Class | Covers |
|-------|--------|
| `BattleVisibilityTests` (8) | `battle_history` hides removed/pending vibes from strangers; still shows for owner/moderator; battle page never returns a hidden battle; cannot vote on a hidden battle (404, no vote); can vote on a fully-public battle. |
| `NotificationControlsTests` (9) | mark-one-read returns unread count; owner-scoped (stranger id → 404); idempotent; mark-all owner-scoped; both endpoints require login; inbox marks-all-on-open; unread context processor counts correctly (and is 0 for anonymous). |
| `StarEndpointGateTests` (3) | anonymous GET/POST → 302 to login; logged-in POST still stars. |
| `AdminTrustWritabilityTests` (2) | admin renders `trust` as read-only; a full admin change POST cannot change `trust`. |

Total tests in the module: **22** (8 battle + 9 notification + 3 star-gate + 2 admin-trust).

Previously added this pass and also relevant: `gallery/test_retention_security.py` initial 20; plus the cache-clear hardening in `gallery/test_security_regressions.py`.

---

## D. Product features added

This pass (and the earlier phases of the same task) added to the already-shipped engagement/retention surface — all built on existing models/helpers, no rewrites:

- **Single-notification mark-read** and **mark-all-read** endpoints with live unread count JSON (nice drop-down / badge integration).
- **`toggle_star` now enforces the same write-gate as `toggle_bookmark`** (consistency, so the "don't break existing features" rule is met across the write surface).
- Existing/verified engagement features (do not duplicate): Follow, Notifications + per-kind prefs, XP/levels/achievements, daily challenges, Battles, PR system, fork/remix, trading, creator analytics, trending, personalized feed, Nolo AI, social creator identity (bio/location/website/github/twitter/avatar).

---

## E. Security scenarios tested

The 10 realistic "somebody made an authorization mistake" scenarios now all pass and are covered both by the original `gallery/test_security_scenarios.py` and the new regression module:

1. **Private project leaks** — feed/search/API/sitemap/detail/related/personalized all exclude non-public; detail is 404 for strangers, 200 for owner.
2. **Pull-request IDOR** — pending-source diff hidden from anonymous/stranger; fork owner/target owner/moderator may review; PR id can't be swapped onto another project; only target owner merges; can't open a PR from someone else's fork; pending target list 404s.
3. **Download/ZIP bypass** — every route to a paid archive asks the same authz; anonymous/non-buyer get no bytes; buyer gets bytes; media URL never streams the paid zip; version download bounces strangers; buyer keeps download through a rescan but only gets the scanned version.
4. **Old URLs after unpublish** — removed/pending invalidates all 7 stale URLs; buyer keeps a purchased download; gone from feed/API/sitemap.
5. **IDOR** — version id, edit/delete/stats, co-owner add, analytics, notifications, saved vibes are all owner-scoped.
6. **Private user data** — git token hashed and never rendered; scan report never in API; trading history own-only; profile exposes no private facts.
7. **Role escalation** — Django `is_staff` grants nothing; moderator ≠ admin; admin ≠ superadmin; moderators can't POST role changes.
8. **Economy races** — double trade charges once; insufficient balance charges nothing; star toggle idempotent/counter never negative; XP can't be farmed; duplicate follow impossible; trade replay-safe.
9. **Git auth** — browser session can't push; wrong user/pass → 401; non-owner with right creds → 403; rotated token stops working; plaintext token never persisted.
10. **Upload/ZIP safety** — path traversal, absolute path, Windows drive path, symlink, `.env`, `node_modules`, executables, >1000 files, zip-bomb ratio all rejected; extraction refuses the same paths; snippet only runs in sandbox; uploaded file preview never executes.

### Part 9 anti-abuse
Rate limits verified: AI readme 10/h, payout 5/h, bookmark 60/h, review 10/h, PR action 20/h (plus the existing login 20/m, pw-reset 10/m, publish 5/h, fork 5/h, comment 10/h, buy 10/h, create_pr 5/h, nolo 20–30/h, follow 30/h, tip 20/h).

---

## F. Existing security issues remaining

**No open P0/P1 issues.** The following are accepted / by-design, with mitigations in place:

- **Arbitrary user JS runs in the preview iframe** — intentionally served inside `<iframe sandbox="allow-scripts">` (opaque origin, no `allow-same-origin`), CSP `sandbox`, short-lived signed token, framing checks. Residual exposure is per-visitor (CPU/CPU-mining/deceptive content), never cross-user cookies/DOM/storage. This is inherent to a live-preview feature.
- **Anonymous download/clone of free published vibes** (`user_can_download` → `True` when cost==0) — intended.
- **OAuth auto-connect** — only connects when the provider-verified email matches; attacker must control that mailbox.
- **Django admin can still modify `status`, `star_cost`, `price_zar` directly** — requires a superuser and flows through publish/scan actions; `trust` is now read-only (this pass), so the pipeline-only moat for the ranking signal holds.

**Recommended residual hardening (would still want, but not "bugs"):**
- Put a real TLS proxy in front and make `SECURE_PROXY_SSL_HEADER` conditional; remove the Redis host port mapping (deployment controls).
- Add `Retry-After` headers on rate-limited 403s, and proxy-aware rate-limit keys in production.

---

## G. Test results

- **Full suite:** `python manage.py test --keepdb` → **811 tests, OK** (was 809 before the +2 admin-trust tests; the one earlier flaky failure is now deterministic and green).
- **Focused:** `gallery.test_retention_security` (22 OK), `gallery.test_security_regressions` (25 OK), `gallery.test_security_scenarios` (77 OK), `users.test_superadmin`, `users.test_admin_dashboard` — 150 tests OK in one batch.
- `python manage.py check` — no issues.

---

## H. Recommended next steps

1. **Deployment hardening (P1):** put a TLS proxy in front, make `SECURE_PROXY_SSL_HEADER` conditional, and remove the Redis host port mapping from the compose file.
2. **Rate-limit polish (P1):** emit `Retry-After` on 403s and switch production keys to proxy-aware (X-Forwarded-For) so real users behind NAT aren't locked out.
3. **Retention (P2):** auto-thumbnails from the sandboxed preview; card metadata (relative time + comment count + "updated"); proxy-aware rate limits.
4. **Retention (P3):** weekly digest ("3 new vibes in games · 1 trade on your vibe"); publish the path to money on the creator dashboard; first-publish bounty so no wallet is ever permanently zero.
5. **Ops:** confirm ClamAV + a Celery worker exist in production (otherwise the "nothing ever publishes, silently" failure returns); `security_check` should fail loudly when production runs console email or has no broker.
