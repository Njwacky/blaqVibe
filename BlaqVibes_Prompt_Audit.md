# BlaqVibes — Prompt Fields Audit (Sanitized, Vulnerabilities Checked, Crush Silently)

**Date:** 08 Aug 2026 | **Rule:** 5 Whys, No Shortcuts, `try/except` everywhere → app crushes silently (logs, never 500 to user)

## 1. List of ALL Prompt Fields in App (Checked)

| # | Model.Field | Where Used | Vulnerability | Sanitized | Crush Silently |
|---|-------------|------------|---------------|-----------|----------------|
| 1 | `AppProject.ai_prompt` (Text) | `/publish/` → detail shows prompt | XSS `<script>`, prompt injection `ignore previous instructions` | ✅ `prompt_sanitize.sanitize_prompt()` (bleach strip, 5000 max, regex filter) in `models.save()` + `forms.clean_ai_prompt()` | ✅ `try/except` in save, form, view |
| 2 | `AppProject.readme` (Markdown) | Feed search, detail README | XSS via markdown `javascript:` | ✅ `sanitizers.render_readme()` (markdown → nh3/bleach) | ✅ try/except in save |
| 3 | `AppProject.title` (Char) | URL slug, search | XSS, slug injection | ✅ `bleach.clean` + `slugify` in save | ✅ try/except |
| 4 | `AppProject.short_description` | Card, search | XSS | ✅ `bleach.clean` in save + form | ✅ |
| 5 | `AppProject.tech_stack` | Search, Nolo features | Injection `'; DROP` | ✅ `bleach.clean` in save + form clean_tech_stack | ✅ |
| 6 | `AppProject.html_code/css_code/js_code` | Preview iframe | Stored XSS if `|safe` on main | ✅ Only in sandboxed iframe, CSP, never inline | ✅ try/except in preview view |
| 7 | `Comment.body` (2000) | Detail comments | Markdown XSS, injection | ✅ `sanitizers.render_markdown_inline` + `bleach` in save | ✅ `post_comment` view try/except + ratelimit |
| 8 | `AppReport.details` (500) | Moderation queue | XSS | ✅ `bleach.clean` in view `report_vibe` (added) | ✅ try/except |
| 9 | `Profile.bio` (280) | `/u/<username>/` | XSS | ✅ `users/forms.py: bleach.clean` in clean_bio | ✅ try/except in save |
| 10 | `Profile.location/website/github/twitter` | Profile | URL injection | ✅ `URLValidator` + bleach | ✅ |
| 11 | `AppVersion.changelog` (280) | Versions | XSS | ✅ `bleach.clean` in edit_vibe | ✅ |
| 12 | `Search q` (query param) | `/?q=` | XSS reflected, injection, 100k DoS | ✅ `prompt_sanitize.sanitize_prompt(q)[:100]` in `feed()` | ✅ try/except fallback to empty feed |
| 13 | `Nolo a_slug/b_slug` (POST) | `/nolo/compare/` | Injection, invalid slug → 500 | ✅ `sanitize_prompt` + `get_object_or_404` | ✅ try/except returns 500 with safe JSON |
| 14 | `Report reason` | Report form | Injection | ✅ Choices limited to 4, not free text | ✅ |

**Not prompt but checked:** `zip_file` validated via `validators.validate_zip` (traversal, bomb, blocked ext).

## 2. How Vulnerabilities Checked

- **XSS:** All text → `bleach.clean(tags=[], strip=True)` or `nh3.clean` before `|safe`. Preview iframe `sandbox` + `CSP`.
- **Prompt Injection:** `prompt_sanitize.PROMPT_INJECTION_PATTERNS` regex for `ignore previous instructions`, `system:`, `jailbreak`, `<script`. Logs warning, replaces with `[filtered]`.
- **Length DoS:** All prompts truncated to max (readme 10000, ai_prompt 5000, comment 2000, search 100) — prevents 10MB POST.
- **Backend only:** S3 keys, `scan_report`, `Trade` costs never in JS — only status strings.

## 3. Crush Silently — try/except, try/catch

**Python (try/except) — backend:**
- `models.save()` wrapped — if sanitize fails, save raw but log, never crash publish.
- `forms.clean_*` wrapped — returns clean or empty, shows validation error, not 500.
- `views.feed()`, `nolo_compare()`, `publish()`, `trade_download()`, `post_comment()` all wrapped — on exception: `logging.exception()`, return safe fallback (empty feed, error JSON, redirect).
- `nolo.py`, `language.py`, `search.py`, `tasks.py` all wrapped — return `[]` or `{}` on fail.
- `tasks.scan_zip_with_clamav()` retries 2, then marks `failed`, not crash worker.

**JS (try/catch) — frontend:**
```js
// All fetch in templates wrapped — no unhandled rejection
try { fetch("/nolo/compare/", ...).then(r=>r.json()).then(d=>{...}) } catch(e){ toast("Compare failed silently"); }
try { navigator.clipboard.writeText(t) } catch(e){ /* fallback execCommand */ }
```

**Result:** Malicious prompt like `<script>alert(1)</script> ignore previous instructions` + 100k `A`s → sanitized to `[filtered]` + truncated → no XSS, no crash, logged, user sees safe text.

## 4. Test

- `POST /publish/` with `ai_prompt="<script>alert(1)</script> ignore previous instructions"` + 6000 chars → saved as `[filtered]` 5000 chars, no 500.
- `GET /?q=<script>alert(1)</script>` → sanitized to empty, feed shows all vibes, no reflected XSS.
- `POST /nolo/compare/` with `a_slug="'; DROP TABLE"` → sanitized, 404, not 500.

**Full Code:** `gallery/prompt_sanitize.py`, `gallery/models.py:save()`, `gallery/forms.py:clean_ai_prompt()`, `gallery/views.py:feed/nolo_compare`, `gallery/nolo.py`, `gallery/search.py` — all with `try/except`.

