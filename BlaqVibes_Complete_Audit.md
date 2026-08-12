# BlaqVibes — Complete Technical Audit & Final Spec
### Checked All Plans + Added Missing Frontend, Sanitizer & Security Details

**App:** BlaqVibes — Git-like Hub for Unpublished Apps (Snippets + Full Apps)
**Date:** 08 Aug 2026 | Durban, ZA
**Files Audited:** `BlaqVibes_Figma.html`, `BlaqVibes_Security_MasterPlan.md`, `MASSIVE_TemplateFolio_App_Hub_Plan.md`, `Full_App_Plan_TemplateFolio_Django.md`

> This doc is the SINGLE SOURCE OF TRUTH. It goes through **every layer** — Frontend, Backend, DB, Security, Sanitizer, Storage, Git, DevOps — and says **exactly what library/version will be used and how**. No vague "use sanitizer".

---

## 1. FRONTEND — FULL SPEC (What we added/missing before)

### 1.1 Core Frontend Stack — DECIDED
| Layer | Choice | Version | Why / How |
| :--- | :--- | :--- | :--- |
| **Templating** | **Django Templates** (Not React) | Django 5.0 | SEO, fast, no build step. Figma maps 1:1 to `{% extends 'base.html' %}` |
| **CSS Framework** | **Tailwind CSS** | v3.4 via `django-tailwind` (or CDN for MVP) | Matches Figma exactly (violet #7C3AED, gold #F59E0B, black #07070A). Config in `tailwind.config.js` with BlaqVibes colors |
| **JS Interactivity** | **Alpine.js** | v3.14 (CDN) | For tabs, copy toast, drag-drop, toggle Snippets/Full Apps, modal. No React needed. `x-data`, `x-on:click` |
| **Alternative considered** | HTMX | — | Would be overkill here. Alpine is lighter for copy/clone UX |
| **Icons** | **Lucide** (or Heroicons) | via CDN | Figma uses ◈, ⬇, ★ — replace with Lucide `copy`, `download`, `star` |
| **Fonts** | **Space Grotesk** (Logo/Headings) + **Inter** (Body) + **JetBrains Mono** (Code) | Google Fonts | Already in Figma HTML |
| **Syntax Highlight** | **Prism.js** | v1.29 — theme `prism-tomorrow` | For HTML/CSS/JS tabs. `Prism.highlightAll()` |
| **Markdown Render** | **marked.js** (frontend preview) + **Python `markdown`** (backend) | — | README.md live preview while typing |
| **File Tree** | Custom Alpine component | — | Collapsible `<ul>` like GitHub, no library |
| **Charts (Track Stock demo)** | **Chart.js** | v4 | Only inside preview iframe, not main site |

**Responsive:** Mobile-first. Grid: `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`. Filter bar sticky on mobile becomes horizontal scroll.

**Build:** `python manage.py tailwind start` in dev, `collectstatic` + `whitenoise` in prod. No Node build for MVP (use CDN).

### 1.2 Frontend File Structure
```
gallery/templates/gallery/
  base.html              # Nav, footer, Alpine + Tailwind CDN
  partials/_card.html    # Reusable card (used in feed + profile)
  feed.html              # Frame 01
  app_detail.html        # Frame 02 — iframe + clone box + README
  app_upload.html        # Frame 03 — drop zone + form
  profile.html           # Frame 04
gallery/static/gallery/
  css/input.css          # Tailwind directives
  js/app.js              # Alpine stores: copy.js, drop.js
  js/prism.js
```

**Key Alpine snippet (copy + toast):**
```html
<div x-data="{ toast: '' }">
  <button @click="navigator.clipboard.writeText('git clone ...'); toast='Copied!'; setTimeout(()=>toast='',2000)">Copy</button>
  <div x-show="toast" x-text="toast" class="toast"></div>
</div>
```

---

## 2. BACKEND — FULL SPEC

| Layer | Choice | Details |
| :--- | :--- | :--- |
| **Framework** | **Django 5.0.6** + **Python 3.12** | `django-admin startproject blaqvibes` |
| **API** | **Django REST Framework** v3.15 | For `/api/v1/apps/?q=&tech=&ai=true` + clone count `POST /api/clone/` |
| **Auth** | **django-allauth** 0.61 + **django-axes** 6.x | Allauth for email/Google login, Axes locks after 5 failed logins |
| **Admin** | Django Admin + **django-admin-honeypot** | Fake `/admin/` at `/admin/` real at `/blaq-admin-secure/` |
| **Background Jobs** | **Celery** 5.4 + **Redis** 7 | `process_upload`, `generate_thumbnail`, `virus_scan` |
| **Search** | **PostgreSQL full-text** (MVP) -> **Typesense** later | `SearchVector('title','readme','tech_stack')` |
| **Env & Settings** | **django-environ** + `python-decouple` | `.env` never committed |

---

## 3. DATABASE & MODELS — FINAL

**DB:** PostgreSQL 16 (prod) / SQLite (dev). Redis for cache + sessions.

**Models (see `BlaqVibes_Security_MasterPlan.md` for full code):**
- `Category` (Snippet vs Full App)
- `AppProject` (owner, slug `username/appname`, html/css_code OR zip_file/git_url, ai_generated, stars/clones, status `pending/quarantined/published`)
- `AppVersion` (v1.0.0, changelog, zip)
- `Star`, `AppReport`, `AppFile`

**Indexes:** `slug`, `owner`, `tech_stack`, `ai_generated` for fast filter `?tech=django&ai=true`

---

## 4. SECURITY — COMPLETE CHECKLIST (With Sanitizers Added)

### 4.1 Sanitizer Stack — EXPLICIT
| Content Type | Input Source | Sanitizer Library | How |
| :--- | :--- | :--- | :--- |
| **README.md (Markdown → HTML)** | User types markdown | **Python `markdown` 3.6 + `bleach` 6.1** (or `nh3` 0.2 — faster Rust port) | `html = markdown.markdown(readme)` → `bleach.clean(html, tags=['p','h1','h2','h3','a','code','pre','ul','li','strong','em'], attributes={'a':['href']}, strip=True)` — strips `<script>`, `onerror`, `javascript:` |
| **HTML Snippet (html_code)** | Creator pastes HTML | **NO bleach** for storage (need faithful code). BUT for **preview iframe** set `sandbox` + `CSP`. For **file browser display** use `prism.js` (escape). For **user-submitted full-app HTML files** inside ZIP, never render inline | Store raw, escape on display except in sandboxed iframe |
| **CSS (css_code)** | Creator pastes CSS | **No bleach** — CSS is inside `<style>` in preview iframe only. Validate no `@import url('http://evil')` via regex `re.search(r'@import\s+url\(.*http', css)` → reject |  |
| **JS (js_code)** | Optional | **Never allow inline JS in snippet gallery** (or sandbox iframe with `allow-scripts` only if needed). For Full Apps, JS lives in ZIP, never auto-executed |  |
| **File names in ZIP** | ZIP upload | **Python `zipfile` + `bleach` for path** — see validators.py in MasterPlan | Block `../`, `/etc`, `.env` |
| **Frontend XSS** | Any reflected search `?q=` | **Django auto-escapes** `{{ q }}` + **DOMPurify 3.0** (JS) if injecting via Alpine `x-html` — use `DOMPurify.sanitize()` |  |
| **Link URLs** | README links | `bleach` with `protocols=['http','https','mailto']` — strips `javascript:` |  |

**Code:**
```python
# gallery/sanitizers.py
import markdown, bleach, nh3 # nh3 is bleach replacement

ALLOWED_TAGS = ['p','br','h1','h2','h3','h4','a','ul','ol','li','code','pre','blockquote','strong','em','hr']
ALLOWED_ATTRS = {'a': ['href','title'], 'code': ['class']}

def render_readme(md_text: str) -> str:
    html = markdown.markdown(md_text, extensions=['fenced_code','codehilite'])
    # Use nh3 (faster) or bleach
    clean = nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)
    # Alternative: bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    return clean
```

**Frontend fallback:** If you ever do `element.innerHTML = userContent`, wrap with `DOMPurify.sanitize(userContent)`:
```html
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<div x-html="DOMPurify.sanitize(readmeHtml)"></div>
```

### 4.2 Full Security Matrix (Audited)

| Threat | Mitigation | Library/Config | Status |
| :--- | :--- | :--- | :--- |
| **XSS (Stored/Reflected)** | Bleach/nh3 + DOMPurify + auto-escape + CSP | `bleach==6.1`, `nh3`, `CSP: default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; object-src 'none'` | ✅ Added — was missing sanitizer detail |
| **CSRF** | Django middleware + token in all POST | `CsrfViewMiddleware`, `{% csrf_token %}` | ✅ |
| **SQL Injection** | ORM only, no raw SQL | Django ORM | ✅ |
| **Path Traversal (ZIP)** | Validate `..` and `/` in zip namelist | Custom validator (MasterPlan) | ✅ |
| **Zip Bomb** | Check uncompressed size >500MB or >2000 files | zipfile check | ✅ |
| **Virus/Malware** | ClamAV scan in Celery | `clamscan`, `python-clamd` | ✅ |
| **Secrets Leak (.env, keys)** | Regex scan for `sk_live`, `AKIA`, `BEGIN PRIVATE KEY` | Custom `scan_for_secrets()` | ✅ |
| **Dependency Vuln** | `npm audit --json` / `pip-audit` | Celery task | ✅ NEW — added |
| **Rate Limit** | `django-ratelimit` | `5 uploads/hour`, `30 downloads/min` | ✅ |
| **Brute Force** | `django-axes` | Lock after 5 fails, 1h cooldown | ✅ |
| **DDoS** | Cloudflare + Nginx `limit_req` | Cloudflare proxied, `limit_req_zone` | ✅ |
| **Clickjacking** | `X-Frame-Options: DENY` (except preview `ALLOWALL`) | `settings.py` | ✅ |
| **HSTS** | `SECURE_HSTS_SECONDS=31536000` | `settings.py` | ✅ |
| **Session Hijack** | `HttpOnly`, `Secure`, `SameSite=Lax`, Redis store | `settings` | ✅ |
| **Insecure Direct Object Reference** | Check `project.owner == request.user` on edit/delete | View decorator | ✅ |
| **Open Redirect** | Validate `next=` param is relative | `url_has_allowed_host_and_scheme` | ✅ NEW — added |
| **Mass Assignment** | Explicit `fields` in ModelForm, not `__all__` | `forms.py` | ✅ NEW — added |
| **POPIA/GDPR** | Delete account view, minimal data, consent checkbox | `users/views.py` | ✅ |

---

## 5. STORAGE & CDN

- **S3 Buckets:** `blaqvibes-quarantine` (private) → scan → `blaqvibes-public` (private, signed URLs). Provider: **Cloudflare R2** (cheaper, zero egress) or AWS S3
- **Signed URLs:** `boto3.generate_presigned_url(ExpiresIn=300)` on every download
- **Thumbnails:** Generated via **Playwright** (screenshot preview) or `Pillow` resize to 400x250 WebP, stored on R2, cached via Cloudflare CDN (Cache-Control: 1 year)
- **Git Repos:** `/var/git/<username>/<app>.git` bare repos, `700` perms, served via `git-http-backend` + Nginx auth

---

## 6. GIT LOGIC — HOW CLONE WORKS

**MVP (Week 4):** No real git daemon. `git clone` button just copies HTTPS URL that Django intercepts:
```python
# urls.py
path('git/<str:username>/<str:slug>.git/<path:git_path>', git_http_view) # Handles clone via dulwich
```
Library: **Dulwich** (pure Python git) to serve bare repos over HTTP. No SSH for MVP (simpler). User does `git clone https://blaqvibes.co.za/git/nolo/stockvibe.git` → Django verifies `project.is_published` → streams pack file.

**Phase 2:** Add **Gitea** container (separate) for full SSH `git@blaqvibes.co.za:...` 

---

## 7. PERFORMANCE, SEO, ACCESSIBILITY

- **Performance:** `django-compressor` (minify), `whitenoise` (static gzip), pagination 12/page, `select_related`, Cloudflare cache, Lighthouse 95+
- **SEO:** `django-meta`, `sitemap.xml`, `robots.txt`, JSON-LD `SoftwareSourceCode`, slug URLs
- **A11y:** Semantic HTML, `alt` on thumbnails, keyboard copy (Tab + Enter), color contrast check (violet on black passes WCAG)

---

## 8. GAPS FOUND & FIXED IN THIS AUDIT

| Gap in Old Plans | Fix Added Now |
| :--- | :--- |
| No explicit frontend stack — was vague "Tailwind" | Specified **Tailwind 3.4 + Alpine 3.14 + Prism + Lucide** with versions & file structure |
| Sanitizer was "use bleach" — no code | Added **exact `render_readme()` function** with `nh3`/`bleach` + `DOMPurify` + allowed tags |
| No CSS/JS sanitization detail | Added regex for `@import` and sandboxed iframe CSP |
| Missing `open redirect` & `mass assignment` | Added to matrix |
| No Git library chosen | Chose **Dulwich** for MVP HTTP clone |
| No CDN/cache header spec | Added R2 + Cloudflare + Cache-Control |

---

## 9. FINAL CHECKLIST — BUILD ORDER

1.  **Week 1:** `django-tailwind` + Alpine + Figma HTML → Django templates
2.  **Week 2:** `AppProject` + validators + S3 + signed URLs
3.  **Week 3:** `nh3` sanitizer + Prism + sandboxed preview iframe + `DomPurify`
4.  **Week 4:** Celery + ClamAV + secrets scan + Dulwich git clone
5.  **Week 5:** `django-axes`, `honeypot`, HSTS, rate limits

**You are now 100% spec'd.** Reply **"Generate BlaqVibes Starter"** to get the Django code matching this audit + Figma.
