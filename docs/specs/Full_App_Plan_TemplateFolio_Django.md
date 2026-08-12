# TemplateFolio — Full App Plan
### A Copy-&-Paste HTML/CSS Template Gallery Built with Django
*Landing Pages • Dashboards • Stock Tracking Pages*

**Date:** 08 Aug 2026 | **Location:** Durban, ZA | **Stack:** Django 5 + PostgreSQL + Tailwind CSS
**Prototype:** `template-gallery-prototype.html` (already in workspace)

---

## 1. EXECUTIVE SUMMARY

**Goal:** Build a platform like HTMLRev / Flowbite / UI Verse but focused on 3 high-demand niches: **Landing Pages, Admin Dashboards, and Stock/Crypto Trackers.** Users browse, see a live preview, and click **Copy HTML** / **Copy CSS** to paste directly into their projects.

**Why Django?** Perfect for this. You need: User management + Admin CMS to paste templates + Categorization + Search + Secure code rendering. Django gives all of this out-of-the-box without needing a SPA.

**Core Value Proposition:** No npm install. No build step. Just copy, paste, ship.

---

## 2. VISION & SUCCESS METRICS

**Vision:** Become the #1 Django-friendly template source for founders and developers in Africa and globally who need clean, dependency-free UI.

**MVP Success (First 60 days):**
- 30 templates uploaded (10 per category)
- <1.5s page load, 100% copy success rate
- 1,000 organic visits via SEO (`free landing page template html css`)
- Admin can add a new template in <3 minutes

**Scale Goal (6 months):**
- 150+ templates, 3 pricing tiers, API access, user accounts & collections

---

## 3. USER ROLES & PERSONAS

| Role | Permissions | Persona |
| :--- | :--- | :--- |
| **Visitor (Anonymous)** | Browse, Search, Filter, Preview, Copy, Download Free | "Thando, junior dev in Durban needs a dashboard for a client by tomorrow, can't pay for Tailwind UI" |
| **Registered User** | + Save Favorites/Collections, Request Template, View history, Pro downloads | "Sarah, indie hacker building stock tracker SaaS" |
| **Admin / Editor** | Django Admin: CRUD Categories/Templates/Tags, Manage Users, Analytics, Approve requests | You |
| **Future: Contributor** | Submit templates for review, earn commission | Community designers |

---

## 4. SITEMAP & INFORMATION ARCHITECTURE

```
/
├── /                                         Gallery (Home) - Filterable Grid
├── /templates/<slug>/                        Detail Page (Preview + Code Tabs + Related)
├── /templates/<slug>/preview/                Isolated Preview (iframe only)
├── /category/<slug>/                         Category Archive (SEO page)
├── /search/?q=dashboard                      Search Results
├── /collections/                             User Saved Templates (login required)
├── /pricing/                                 Free vs Pro
├── /request/                                 Request a template
├── /accounts/login, /register, /profile      Auth
└── /admin/                                   Django Admin CMS
```

**SEO Pages to auto-generate:** `/category/landing-pages/`, `/category/dashboard/`, `/category/track-stock/`, `/tag/tailwind/`, `/tag/dark-mode/`

---

## 5. FEATURE BREAKDOWN (Phased)

### PHASE 1 — MVP (Weeks 1-4) — MUST HAVE
- [ ] Category system (Landing, Dashboard, Track Stock) + Tags (Tailwind, Bootstrap, Dark, Light)
- [ ] Template CRUD via Django Admin (html_code, css_code, thumbnail, category, tags, is_pro)
- [ ] Public Gallery: Grid, Category filter (query param), Search (icontains), Pagination (12/page)
- [ ] Detail Page: Live Preview in `iframe`, HTML/CSS tabs (Prism.js), **Copy buttons** (navigator.clipboard), Download .html file
- [ ] Responsive, dark-mode gallery UI (Tailwind)
- [ ] SEO fundamentals: slug URLs, meta description, sitemap.xml

### PHASE 2 — GROWTH (Weeks 5-6)
- [ ] User Auth (django-allauth): Register/Login, Favorites/Bookmark, Collections
- [ ] Pro Paywall: `is_pro` flag + Paystack / Stripe, `paystack` is key for ZA
- [ ] View counter, Like counter, Copy counter
- [ ] Related templates (same category)
- [ ] Request template form
- [ ] Admin analytics dashboard (copies/day, top templates)

### PHASE 3 — SCALE (Weeks 7-8+)
- [ ] User-submitted templates (moderation queue)
- [ ] REST API: `GET /api/templates/?category=dashboard` for external devs
- [ ] CLI: `npx templatefolio add dashboard-01`
- [ ] Comments / Ratings
- [ ] Figma to HTML importer
- [ ] AI search: "give me a dark dashboard with sidebar"

---

## 6. DATABASE SCHEMA (PostgreSQL)

```python
# gallery/models.py

class Category(models.Model):
    name = models.CharField(max_length=100) # Landing Page
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=30, blank=True) # e.g. "layout"
    order = models.PositiveIntegerField(default=0)

class Tag(models.Model):
    name = models.CharField(max_length=50) # Tailwind, Bootstrap, Dark Mode
    slug = models.SlugField(unique=True)

class Template(models.Model):
    title = models.CharField(max_length=200) # e.g. "SaaS Launch Hero v1"
    slug = models.SlugField(unique=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='templates')
    tags = models.ManyToManyField(Tag, blank=True)
    
    description = models.TextField(help_text="SEO + card excerpt")
    html_code = models.TextField()
    css_code = models.TextField(blank=True)
    js_code = models.TextField(blank=True) # optional for stock charts
    
    thumbnail = models.ImageField(upload_to='thumbnails/', blank=True, null=True)
    # For fast gallery load, auto-generate 400x250 WebP via django-imagekit
    
    is_pro = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    
    views = models.PositiveIntegerField(default=0)
    copies = models.PositiveIntegerField(default=0) # increment on copy
    likes = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['slug']), models.Index(fields=['category','is_published'])]

class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    template = models.ForeignKey(Template, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = ('user','template')

class TemplateRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)
    details = models.TextField()
    is_done = models.BooleanField(default=False)
```

**ERD:** Category 1—* Template *—* Tag | User 1—* Favorite *—1 Template

---

## 7. TECH STACK — DECIDED

| Layer | Choice | Why |
| :--- | :--- | :--- |
| **Backend** | **Django 5.0 + Python 3.12** | Admin, ORM, Auth out-of-box |
| **DB** | **PostgreSQL** (SQLite for dev) | Full-text search, better for prod |
| **Frontend** | **Django Templates + Tailwind CSS CDN + Alpine.js** | No React needed. Alpine for tabs/copy. Fastest to ship. |
| **Syntax Highlight** | **Prism.js** (`prism-tomorrow` theme) | Lightweight, copy-friendly |
| **Image** | **Pillow + django-imagekit** | Auto thumbnail WebP |
| **Search** | **Postgres `SearchVector` (Phase 1) → Typesense/Meilisearch (Phase 3)** | No extra service at start |
| **Auth** | **django-allauth** | Social login ready |
| **Payments ZA** | **Paystack** (or Stripe if international) | Paystack supports ZAR, EFT |
| **Deployment** | **Render.com / Railway / DigitalOcean App Platform** + Cloudinary/S3 for media | Free tier to start |
| **Analytics** | **Plausible** (privacy) + Django `copies` counter |  |

**Alternative considered:** Django + HTMX (overkill for MVP), Django REST + Next.js (too heavy).

---

## 8. PROJECT STRUCTURE (Final)

```
core/                  # Project settings
├── settings.py
├── urls.py
└── wsgi.py
gallery/               # Main app
├── models.py
├── views.py
├── urls.py
├── admin.py           # Custom admin with code preview
├── templatetags/
├── templates/gallery/
│   ├── base.html
│   ├── partials/_card.html
│   ├── template_list.html
│   ├── template_detail.html
│   └── preview.html   # Isolated - NO base.html
├── static/gallery/
│   ├── css/input.css  # Tailwind
│   └── js/copy.js
users/                 # Favorites, Profile
api/                   # DRF (Phase 3)
media/thumbnails/
db.sqlite3
requirements.txt
```

---

## 9. KEY PAGE WIREFRAMES & LOGIC

### A. Gallery (`/`) - template_list.html
- **Top:** Search bar + Category Chips (All | Landing | Dashboard | Track Stock) - active via `request.GET.category`
- **Grid:** 3 columns desktop, 1 mobile. Card = thumbnail (or mini live render), title, category badge, likes, copies
- **Performance:** `select_related('category').prefetch_related('tags')` + pagination. Thumbnails lazy-load.
- **Filter Logic (views.py):**
  ```python
  templates = Template.objects.filter(is_published=True)
  if cat := request.GET.get('category'): templates = templates.filter(category__slug=cat)
  if q := request.GET.get('q'): templates = templates.filter(title__icontains=q) | filtering via SearchVector
  ```

### B. Detail (`/templates/<slug>/`) - The Money Page
Layout (2-column desktop, stacked mobile):
- **Left (60%):** `iframe src="{% url 'preview' slug %}"` height 520px, border-radius 16px, toolbar: "Preview" | "Mobile / Desktop" toggle
- **Right (40%):** Title, Description, Tags, Stats (👁 1.2k  ⎘ 340 copies), **Tabs: HTML | CSS | JS** + **Copy Button** (changes to "✓ Copied!" for 2s)
- **Below:** Download buttons: `Download HTML`, `Download ZIP (html+css)`, `Copy All`
- **Logic:** Increment `views` on page load (via F() atomic), increment `copies` via AJAX `POST /api/copy/<id>/` when user clicks copy

### C. Preview (`/preview/<slug>/`) - CRITICAL
Must be **isolated**: No `base.html`, no site nav. Only:
```html
<!doctype html><html><head><meta name="viewport"><style>{{ template.css_code|safe }}</style></head>
<body>{{ template.html_code|safe }}<script>{{ template.js_code|safe }}</script></body></html>
```
Add `sandbox="allow-scripts"` to iframe for security.

---

## 10. COPY-PASTE IMPLEMENTATION (Deep Dive)

**Frontend (`static/gallery/js/copy.js`):**
```javascript
async function copyCode(elementId, templateId, type){
  const code = document.getElementById(elementId).innerText;
  await navigator.clipboard.writeText(code);
  // 1. UI feedback
  showToast(`${type} copied!`);
  // 2. Analytics
  fetch(`/api/templates/${templateId}/copied/`, {method:'POST', headers:{'X-CSRFToken': csrftoken}});
}
```
**Backend:** Small view to increment `copies` and log event. Fallback for older browsers: `document.execCommand('copy')`.

**UX Enhancements:**
- Show line numbers (Prism plugin)
- "Copy with Tailwind CDN" checkbox for templates that need `<script src="https://cdn.tailwindcss.com"></script>`
- Keyboard shortcut: `Cmd+Shift+C`

---

## 11. DJANGO ADMIN CUSTOMIZATION (Your CMS)

In `gallery/admin.py`:
- `list_display = ['thumbnail_preview','title','category','is_pro','copies','is_published']`
- `list_filter = ['category','is_pro','tags']`
- `prepopulated_fields = {'slug': ('title',)}`
- `CodeMirror widget` for `html_code` / `css_code` (use `django-codemirror` or just `<textarea class="code">` with Prism)
- Action: "Duplicate template", "Make Pro/Free"
- Inline preview: admin shows live iframe preview on edit page

This is how you will add 30 templates fast: Copy from Figma/Tailwind UI → Paste into Admin → Save → Instantly live.

---

## 12. SECURITY & PERFORMANCE

- **XSS:** Preview is sandboxed. Never render `html_code` with `|safe` outside preview iframe without sanitization (use `bleach` if allowing user submissions).
- **CSRF:** All POSTs protected. Copy endpoint uses POST + CSRF.
- **Image:** Validate upload (max 2MB, WebP conversion)
- **Caching:** `@cache_page(60*15)` for gallery + template detail; cache invalidation on save via signals
- **Performance Target:** Lighthouse >90. Use `django-compressor`, lazy thumbnails, pagination, `defer` JS.

---

## 13. SEO PLAN

- Clean slugs: `/templates/dark-saas-dashboard/`
- `sitemap.xml` via `django.contrib.sitemaps`
- `robots.txt`
- Meta: `<title>{{ template.title }} - Free HTML CSS | TemplateFolio</title>` + description 155 chars
- JSON-LD `SoftwareSourceCode` schema
- Category pages target keywords: "free landing page templates html css", "admin dashboard template free", "stock tracker html template"

---

## 14. MONETIZATION (ZA Friendly)

| Tier | Price | Access |
| :--- | :--- | :--- |
| **Free** | R0 | 60% templates, copy + preview, with attribution |
| **Pro** | R149/mo or R999 lifetime | All templates, ZIP download, no attribution, Figma files, Priority requests |
| **Team** | R399/mo | 5 seats + API |

**Payment:** Paystack Checkout. Webhook `paystack/webhook/` → set `user.is_pro = True`. Gate in view: `if template.is_pro and not request.user.is_pro: show paywall modal`.

---

## 15. DEPLOYMENT CHECKLIST (Render)

1. `pip freeze > requirements.txt` (Django, psycopg2, gunicorn, whitenoise, Pillow)
2. `settings.py`: `DEBUG=False`, `ALLOWED_HOSTS`, `WhiteNoise` for static, `DATABASE_URL` via `dj-database-url`
3. `python manage.py collectstatic`, `migrate`
4. Create Superuser, add Categories via Admin
5. Media: Cloudinary or AWS S3 (Render disk is ephemeral)
6. Domain: `templatefolio.co.za` + Cloudflare

**Dev to Prod command:** `git push origin main` → auto deploy

---

## 16. 8-WEEK TIMELINE & MILESTONES

| Week | Milestone | Deliverable |
| :--- | :--- | :--- |
| **Week 1** | Foundation | Django project, models, admin, Tailwind setup, 2 templates dummy data |
| **Week 2** | Gallery & Preview | `template_list` + filters + search + `preview` iframe working, responsive |
| **Week 3** | Detail & Copy | Detail page with tabs, Prism.js, copy + download ZIP, view/copy counters |
| **Week 3-4** | Content Sprint | Upload **30 templates** (10/10/10) + SEO pages, test on mobile |
| **Week 5** | Auth & Favorites | django-allauth, Bookmark button, /collections page |
| **Week 6** | Payments | Paystack integration, Pro gate, Pricing page |
| **Week 7** | Polish & SEO | Sitemap, Analytics, Performance audit, Plausible, Lighthouse 95+ |
| **Week 8** | Launch | Deploy to Render, Product Hunt, Reddit r/webdev, ZA Facebook groups |

**Daily Workflow (Week 1-2):** Code 3h + Add 2 templates 1h

---

## 17. WHAT YOU SHOULD DO TOMORROW (Next 3 Actions)

1. **Run the scaffold:** Say "Generate starter" — I will create the runnable Django code in your workspace (`core/`, `gallery/`) so you can `python manage.py runserver` immediately.
2. **Pick 3 starter templates:** Choose 1 Landing, 1 Dashboard, 1 Stock example from Tailwind UI / Flowbite to copy-paste into Admin as your first rows.
3. **Set up Paystack test keys:** Create paystack.co account (free) — needed for Week 6 but good to have now.

---

## 18. RISKS & MITIGATIONS

- **Risk:** CSS conflicts in preview → **Mitigation:** Iframe isolation + reset CSS
- **Risk:** Copy fails on iOS → **Mitigation:** Fallback `execCommand` + test on real device
- **Risk:** Content creation bottleneck → **Mitigation:** Batch-create templates on weekends, use AI to convert screenshots to HTML
- **Risk:** Hosting costs → **Mitigation:** Start on Render free tier + Cloudinary free 25GB

---

### Want me to turn this plan into code?
Reply **"Generate the starter"** and I will:
- Create the full Django project with models, views, urls, admin, templates, and Tailwind already wired
- Seed it with the 6 templates from your prototype (so you can run it instantly)
- Give you a `README.md` with `pip install` → `runserver` in 2 commands

Your prototype `template-gallery-prototype.html` is already a pixel-perfect frontend — the Django backend just makes it dynamic and copyable at scale.

