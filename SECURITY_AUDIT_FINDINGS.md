# BlaqVibes — Adversarial Security Audit

**Scope:** the whole Django application (`gallery/`, `users/`, `blaqvibes/`), templates, settings, and `docker-compose.yml`.
**Method:** manual source review of every auth gate, every data-access path (downloads, file preview, git daemon, PRs, forks, profiles, wallet, payouts), and every deployment control. No runtime exploit was attempted (deps not installed in this sandbox); findings are from static analysis and are tied to file:line evidence.

**TL;DR** — This is an unusually well-hardened codebase (extensive "5 Whys" commentary shows it has already survived multiple security passes). I found **no trivial auth bypass** on the core money/download path. What remains is a small number of residual gaps, the most serious being a broken-access-control (IDOR) on the Pull Request pages that leaks **unpublished project source code** to anonymous visitors.

---

## Findings summary

| # | Finding | Severity |
|---|---------|----------|
| 1 | PR detail/list/network ignore the published/visibility gate — anonymous read of unpublished fork source | **High** |
| 2 | Login + password-reset endpoints are not rate-limited (brute force / email bombing) | Medium |
| 3 | `SECURE_PROXY_SSL_HEADER` trusts a client-supplied header with no TLS proxy in compose → HTTPS enforcement bypass | Medium |
| 4 | Redis published to the host with no auth → rate-limit defeat + broker tampering | Medium |
| 5 | `/readyz` leaks DB / Redis connection details to unauthenticated callers | Low |
| 6 | `download_version` and `report_vibe` are missing the status gate | Low |
| 7 | Weak default `POSTGRES_PASSWORD` / `blaq` credentials | Low |
| 8 | `csp-report` endpoint accepts unauthenticated log flooding | Info |
| 9 | ~~Review body never renders~~ — **retracted**: line 846 is a comment *reply* (`Comment.body_html` exists and is populated); the reviews loop already renders `r.text`. No bug. | — |

---

## Remediation status (step 2 — applied)

Fixes are implemented on this branch and covered by `gallery/test_security_regressions.py`. `python manage.py test gallery users` passes **638 tests** (617 pre-existing + 21 new).

| # | Fixed in | Change |
|---|----------|--------|
| 1 | `gallery/views_community.py` (`pr_list`, `pr_detail`), `gallery/views.py` (`fork_network`) | `pr_list`/`fork_network` fetch with `status='published'` (both the main path and the crush-fallback); `pr_detail` gates on `user_can_see_project(request.user, pr.source)` (plus the PR target's owner) **outside** the `try/except` so `Http404` propagates instead of the fallback re-fetching ungated |
| 2 | `blaqvibes/urls.py` | `login_view`, `password_reset_view`, `password_reset_confirm_view` wrapped in `@ratelimit(key='ip', rate=…, method='POST')` (20/m, 10/m, 20/m) |
| 3 | `blaqvibes/settings.py` | `SECURE_PROXY_SSL_HEADER` / `USE_X_FORWARDED_HOST` / `USE_X_FORWARDED_PORT` now set only when `PREVIEW` or `DJANGO_BEHIND_TLS_PROXY=1` (the docker-compose posture leaves the header untrusted) |
| 4 | `docker-compose.yml` | removed the `6379:6379` host port mapping; documented the `--requirepass` opt-in for exposure |
| 5 | `gallery/health.py` | `_db_ok`/`_queue_state` return static `'unavailable'` labels; the real exception is `logger.exception`-ed server-side only |
| 6 | `gallery/views.py` (`download_version`, `report_vibe`) | `download_version` 404s unless `user_can_see_project` or `user_can_download`; `report_vibe` fetches with `status='published'` **outside** the `try/except` |
| 7 | `docker-compose.yml` | documented the strong-password requirement next to the fallback (still env-driven) |
| 8 | `gallery/csp_views.py` | `@ratelimit(key='ip', rate='60/m', method='POST')` on `csp_report` |
| 9 | — | retracted, no change (see above) |

**Regression tests** (`gallery/test_security_regressions.py`): PR diff/list/network visibility for anonymous/stranger/fork-owner/target-owner/moderator; `download_version` pending-gate; `report_vibe` pending-404; login (20/m) + password-reset (10/m) rate limits incl. GET-unlimited; csp-report flood (60/m) + valid-report 204; `/readyz` exception-string non-disclosure; `SECURE_PROXY_SSL_HEADER` default-vs-proxy posture via a controlled subprocess import.

---

## 1. HIGH — Pull-request pages bypass the published/visibility gate (IDOR)

**Files:** `gallery/views_community.py:301` (`pr_list`), `:312` (`pr_detail`); `gallery/views.py:1306` (`fork_network`).

Every other content view guards visibility through `user_can_see_project()` (which returns `True` only for `status='published'`, the owner, or a moderator). These three do **not**:

```python
# gallery/views_community.py
def pr_list(request, slug):
    target = get_object_or_404(AppProject, slug=slug)          # ← no status filter
    prs = PullRequest.objects.filter(target=target)...
    return render(request, 'gallery/pr_list.html', ...)

def pr_detail(request, slug, pr_id):
    target = get_object_or_404(AppProject, slug=slug)          # ← no status filter
    pr = get_object_or_404(PullRequest, id=pr_id, target=target)
    diff = diff_projects(pr.source, pr.target)                 # ← reads real ZIP bytes
    nolo_diff = compare_apps(pr.source, pr.target)['diff']
    return render(request, 'gallery/pr_detail.html', ...)
```

```python
# gallery/views.py
def fork_network(request, slug):
    root = get_object_or_404(AppProject, slug=slug)            # ← no status filter
    ...
```

**What an attacker can do (no login required):**

1. Pick any published vibe's slug (visible on the public feed).
2. GET `/app/<slug>/prs/` — lists every open PR with its title, description, author, source-fork title and **sequential integer PR id**.
3. GET `/app/<slug>/prs/<id>/view/` — renders a **line-by-line content diff** of `pr.source` (the fork, which is in `pending` state and therefore *not supposed to be public*) against the target. `diff_projects()` reads the ZIPs through `gallery/diff.py` and returns actual file contents; `compare_apps()` additionally leaks the source fork's `title`, `slug`, `tech_stack`, `language_stats`, `file_count`, and `stars`.

This breaks the app's own confidentiality rule (stated in `access.py` and `scan_status`'s 5-Whys: *"A guessed slug leaks queued/quarantined… 403 confirms the vibe exists"*) — elsewhere the app even returns 404 rather than 403 so pending slugs are not confirmable. Here, pending forks and their full source are readable by anyone.

**Fix:**
```python
def pr_list(request, slug):
    target = get_object_or_404(AppProject, slug=slug, status='published')
    ...
def pr_detail(request, slug, pr_id):
    target = get_object_or_404(AppProject, slug=slug, status='published')
    ...
    if not user_can_see_project(request.user, pr.source):
        raise Http404
    ...
```
(`fork_network` should likewise start from a published root.)

---

## 2. MEDIUM — No rate limiting on login and password-reset

**Files:** `blaqvibes/urls.py:44` (`login_view`), `:119` (`PasswordResetView`).

`signup` is limited to `10/h` per IP, and most other POSTs carry `@ratelimit`. But the two endpoints that matter most for account compromise are wide open:

- `LoginView` (username/email + password) — unlimited guesses ⇒ **credential brute force / password spraying**.
- `PasswordResetView` — unlimited POSTs ⇒ **reset-email bombing** of any known address (Django correctly returns a generic response, so no username enumeration, but the email send itself is unbounded).

The site's `handler403`/`safe_403` already renders friendly 403s, so wrapping these views with `@ratelimit(key='ip', rate='...', method='POST')` is a drop-in. Django's admin login at `/blaq-admin-secure/` has the same exposure (defense-in-depth: also apply `django-ratelimit` there, or leave the admin behind an IP allow-list).

**Fix:** add `@ratelimit(key='ip', rate='20/m', method='POST')` (block=True) to the login, password-reset, and `PasswordResetConfirmView` views.

---

## 3. MEDIUM — `SECURE_PROXY_SSL_HEADER` trusts a spoofable header with no proxy in the compose file

**File:** `blaqvibes/settings.py:481`

```python
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
```

This makes `request.is_secure()` return `True` whenever the *client* sends `X-Forwarded-Proto: https`. The shipped `docker-compose.yml` runs `gunicorn` directly on `:8000` with **no nginx/TLS terminator in front** that would overwrite or strip this header. Consequences in the production posture:

- `SECURE_SSL_REDIRECT = True` stops upgrading plaintext requests (Django thinks it's already HTTPS), so the site is served over plain HTTP on `:8000`.
- `safe_internal_next(..., require_https=request.is_secure())` and CSRF origin checks treat the connection as secure.

This is the classic "only set this header when behind a trusted proxy that *replaces* it" mistake. It is only safe when a proxy in front unconditionally overwrites the header.

**Fix:** add an nginx/traefik TLS terminator and configure it to overwrite `X-Forwarded-Proto`; or only set `SECURE_PROXY_SSL_HEADER` when an env flag (`BEHIND_PROXY=1`) is present. At minimum, document that `:8000` must never be reachable directly.

---

## 4. MEDIUM — Redis published to the host with no authentication

**File:** `docker-compose.yml:21`

```yaml
redis:
  image: redis:7-alpine
  ports: ["6379:6379"]      # binds 0.0.0.0 on the host
  # no `command: redis-server --requirepass ...`
```

Redis is three things at once here: the Celery broker, the shared rate-limit cache (`RATELIMIT_CACHE`), and (optionally) the general cache. Redis 7's default `protected-mode yes` rejects non-loopback clients *until* the operator binds it or sets a password, so this is partially mitigated out-of-the-box — but the port mapping is unnecessary (services only need the compose network) and is one config change away from being open. If reachable:

- **Flush / rewrite the rate-limit cache** ⇒ defeats every `@ratelimit` (signup spam, brute force, git/upload limits).
- **Tamper with or delete the Celery broker queue** ⇒ deny the scan pipeline (DoS) or inject task messages.
- Read any cached values (cache key `blaqvibes-*`).

**Fix:** remove `ports: ["6379:6379"]` (and optionally set `command: redis-server --requirepass ${REDIS_PASSWORD}`), keep it on the internal network only. Do the same for the Postgres service if it is ever given a port mapping.

---

## 5. LOW — `/readyz` leaks DB/Redis connection details

**File:** `gallery/health.py:72` (`readiness`) → `_db_ok()` (`:38`) and `_queue_state()` (`:48`).

The unauthenticated readiness endpoint returns raw exception strings in its JSON body:

```python
return False, f'{type(exc).__name__}: {str(exc)[:200]}'        # database
return False, f'redis unreachable: {str(exc)[:200]}'           # queue
```

Postgres connection errors embed host/port/database/user; Redis errors embed the broker host. An attacker can map internal hostnames from a public, `no-store` JSON endpoint. Liveness correctly returns nothing sensitive; readiness should do the same.

**Fix:** return a boolean + a static label (`'ok'` / `'unavailable'` / `'error'`) and log the real exception server-side only.

---

## 6. LOW — `download_version` and `report_vibe` skip the status gate

**Files:** `gallery/views.py:1256` (`download_version`), `:1020` (`report_vibe`).

- `download_version` uses `get_object_or_404(AppProject, slug=slug)` with **no** `status` filter. The `@login_required` + `user_can_download()` check still applies, so a stranger can't pull it, but it lets an owner (or a buyer with a Trade/Sale receipt) fetch *historical* ZIP versions of a `removed`/`pending` project — inconsistent with `download_zip`, which explicitly limits itself to `status__in=['published', 'removed']`.
- `report_vibe` uses `get_object_or_404(AppProject, slug=slug)` with no status filter, so anyone can create `AppReport` rows against pending/quarantined/removed vibes (spam the moderation queue with reports on hidden content). Low impact, but the same "guessed slug must not confirm existence" principle applies.

**Fix:** add `status='published'` to both lookups (and for `report_vibe`, drop or 404 silently for non-visible projects).

---

## 7. LOW — Weak default DB credentials

**File:** `docker-compose.yml:8`

```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-blaq123}
```

`blaq` / `blaq123` is a documented default. The DB has no port mapping today (so exposure is limited to the compose network), but this is a footgun: the moment a port mapping is added for debugging, the database is open with a trivial password. Also `POSTGRES_DB`/`USER`/`PASSWORD` are duplicated across `web`, `celery`, and `celery-beat` services.

**Fix:** require `POSTGRES_PASSWORD` (fail if unset), generate a strong one in `.env`, and consider a secrets file instead of an inline default.

---

## 8. INFO — Unauthenticated `csp-report` flooding

**File:** `gallery/csp_views.py` — `@csrf_exempt def csp_report` logs any POST body to Sentry and returns 204.

An attacker can POST arbitrary garbage to `/csp-report/` to flood Sentry (log/ingest cost + noise that can bury real CSP violations). `@ratelimit(key='ip', rate='…')` here would bound it, and the handler should ignore bodies that don't parse as a valid `csp-report` document.

---

## 9. Retracted — not a bug (was: "review text never renders")

**Retracted after re-verification.** `templates/gallery/app_detail.html:846` renders `{{ r.body_html|safe }}`, but that line is inside the **comment-reply** loop (`{% for r in c.replies.all %}`), where `r` is a `Comment` — and `Comment` (`gallery/models.py`) *does* have `body_html`, populated in `Comment.save()` via `render_markdown_inline`. The actual reviews loop (line 1145–1179) correctly renders `{{ r.text|default:"—" }}`, and `Review.save()` already `sanitize_prompt`-cleans and profanity-blanks `text`. No change needed.

---

## Accepted / by-design risks (for awareness, not bugs)

These are intentional product decisions with the stated mitigations already in place — listed so you know they were considered:

- **Arbitrary user JS executes in the preview iframe.** `snippet_doc` / `run_static` serve user HTML/JS inside `<iframe sandbox="allow-scripts">` (opaque origin, no `allow-same-origin`), with CSP `sandbox`, a short-lived signed token, and `Sec-Fetch-Dest`/Referer framing checks. User scripts run on *visitors'* machines (CPU mining, deceptive content) but cannot reach cookies/DOM/storage. This is inherent to a "live preview" feature; the residual exposure is per-visitor, not cross-user.
- **Anonymous download/clone of *free* published vibes** (`user_can_download` returns `True` when cost==0) — intended.
- **OAuth auto-connect** (`SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True`) — standard allauth behaviour; only connects when the provider-verified email matches, which requires the attacker to control that mailbox at the provider.
- **Django admin (`/blaq-admin-secure/`) can edit `AppProject.status`, `trust`, `star_cost`, `price_zar`** directly (only `stars_balance` is read-only on `ProfileAdmin`). This bypasses the "pipeline-only writer" rule for `trust` — acceptable only because it requires a superuser, but worth locking `trust` read-only in `AppProjectAdmin` too.

---

## Suggested priority order

1. **Fix the PR IDOR (#1)** — it's the only issue where an unauthenticated user reads data the app explicitly classifies as private.
2. **Rate-limit login/reset (#2)** — cheap, high return.
3. **Put a real TLS proxy in front and make `SECURE_PROXY_SSL_HEADER` conditional (#3)**, and **remove the Redis port mapping (#4)** — these are deployment controls.
4. Then the low-severity cleanups (#5–#8).
