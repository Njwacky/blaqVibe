# TemplateFolio MASSIVE PLAN v2.0 — From Template Gallery to Git-Like App Hub
### Django App for HTML/CSS Templates + Full Unpublished Apps (AI & People Built)

**Date:** 08 Aug 2026 | Durban, ZA
**Correction Applied:** `people` not Apple — massive community of creators with AI-generated & hand-coded apps that never got published
**Prototype:** `template-gallery-prototype.html` + `Full_App_Plan_TemplateFolio_Django.md` (v1)

---

## 1. NEW VISION: TWO PRODUCTS IN ONE DJANGO APP

You were right — this is MASSIVE if you do it correctly. Don't just do copy-paste snippets. Build **GitHub + Template Gallery Hybrid**.

| Layer | What it is | Example |
| :--- | :--- | :--- |
| **LAYER 1: Snippets (v1)** | Copy-paste HTML/CSS sections | Landing hero, dashboard card, stock table row |
| **LAYER 2: FULL APPS (v2 - MASSIVE)** | Downloadable / Clonable full projects like Git | A complete AI-built "Stock Tracker SaaS" (Django + React) that someone built but never deployed, or a "Restaurant Landing + Ordering System" |

**User Journey:** Browse landing page template -> Love it -> See "Get Full App" button -> Download ZIP or `git clone https://templatefolio.co.za/git/sarah/stock-tracker.git` -> Run locally.

This solves the real problem: People (especially with AI like v0, Lovable, ChatGPT) are generating **hundreds of apps that just sit on their laptops**. They need a place to upload, share, get stars, and let others download.

---

## 2. HOW PEOPLE WILL UPLOAD & SHARE (Like Git)

You need 3 upload methods, all feeding the same Django model:

### Method A: Simple ZIP Upload (for non-devs / AI apps) - 80% of users
1. User clicks **"Publish App"**
2. Drag & drop ZIP (or folder) — e.g. `my-stock-app.zip`
3. Django: `unzip -> scan -> store on S3 -> create Git repo automatically in background`
4. App page is live instantly with Preview, README, Download ZIP button

### Method B: Git Push (for devs) - Like GitHub
1. User creates app: `my-dashboard`
2. Django gives them a Git URL: `git@templatefolio.co.za:thando/my-dashboard.git`
3. They do on their laptop:
```bash
git remote add templatefolio git@templatefolio.co.za:thando/my-dashboard.git
git push templatefolio main
```
4. Django (via Gitea/Gogs integration or simple `git bare repo` on server) receives push, updates files, rebuilds preview.

### Method C: AI Import (Future)
- Paste Lovable / v0 / ChatGPT share link -> Django fetches files via API -> Auto-creates app

**Download for visitors:**
- **Download ZIP** button (always)
- **Clone:** `git clone https://templatefolio.co.za/git/thando/my-dashboard.git`
- **Use Template:** For snippet-level templates, keep the one-click `Copy HTML` button

---

## 3. UPDATED DATABASE SCHEMA - BUILT FOR MASSIVE SCALE

This replaces the v1 schema. It handles both snippets AND full apps.

```python
# gallery/models.py - EXPANDED

class Category(models.Model):
    name = models.CharField(max_length=100) # Landing, Dashboard, Track Stock, Full SaaS, AI Generated
    slug = models.SlugField(unique=True)
    type = models.CharField(choices=[('snippet','Snippet'), ('full_app','Full App')])

class AppProject(models.Model): # RENAMED from Template - this is the REPO
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    title = models.CharField(max_length=200) # e.g. "AI Stock Portfolio Tracker"
    slug = models.SlugField(unique=True) # thando/stock-tracker
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    
    # The two types
    project_type = models.CharField(choices=[('snippet','HTML/CSS Snippet'), ('full_app','Full Downloadable App')])
    
    short_description = models.CharField(max_length=200) # For cards
    readme = models.TextField(blank=True, help_text="Markdown - rendered as README.md")
    
    # For SNIPPETS (Layer 1)
    html_code = models.TextField(blank=True)
    css_code = models.TextField(blank=True)
    
    # For FULL APPS (Layer 2) - Git-like
    git_url = models.CharField(max_length=400, blank=True) # git@...
    zip_file = models.FileField(upload_to='apps/zips/', blank=True) # S3
    repo_path = models.CharField(max_length=500, blank=True) # /var/git/thando/app.git on server
    tech_stack = models.CharField(max_length=200, blank=True) # e.g. "Django, React, Tailwind"
   ai_generated = models.BooleanField(default=False)
    ai_tool = models.CharField(max_length=50, blank=True) # Lovable, v0, ChatGPT

    # Thumbnails & Preview
    thumbnail = models.ImageField(upload_to='thumbnails/')
    preview_url = models.URLField(blank=True) # Live demo link if deployed

    # Git-like stats
    views = models.PositiveIntegerField(default=0)
    clones = models.PositiveIntegerField(default=0) # git clones + ZIP downloads
    copies = models.PositiveIntegerField(default=0) # for snippets
    stars = models.PositiveIntegerField(default=0)
    
    is_published = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class AppVersion(models.Model): # Like git commits/releases
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='versions')
    version = models.CharField(max_length=20) # v1.0.0
    changelog = models.TextField(blank=True)
    zip_file = models.FileField(upload_to='apps/versions/')
    created_at = models.DateTimeField(auto_now_add=True)

class Star(models.Model): # Like GitHub stars
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE)
    class Meta: unique_together = ('user','project')

class AppFile(models.Model): # For file browser like GitHub
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='files')
    path = models.CharField(max_length=500) # templates/index.html
    size = models.PositiveIntegerField()
```

**This schema lets you:** Show a file tree (`/app/thando/stock-tracker/files/`), count clones, and handle both a 20-line HTML snippet and a 50MB full AI app.

---

## 4. ARCHITECTURE FOR MASSIVE SCALE (Django Can Handle It, But You Need This)

Don't run Git inside Django directly for v1. Use this simple, scalable hybrid:

```
[ User Browser ]
      |
      v
[Django Web App (Render/Railway)]  <-- Serves gallery, auth, stars, search
      |  \
      |   \--> [PostgreSQL] (projects, users, stars)
      |    \-> [Redis + Celery] (background unzip, thumbnail generation, git create)
      |     \-> [S3 / Cloudinary / AWS S3] (ALL zip files, thumbnails - NEVER on local disk)
      |
      +--> [Git Server] : Option 1: Simple Bare Repos on same server (/var/git/)
                         Option 2 (PRO - Later): Self-hosted Gitea instance on separate droplet
                         Django just stores the `git_url`, Gitea handles the actual git protocol
      |
      +--> [CDN - Cloudflare] (caches thumbnails, ZIP downloads - critical for massive)
```

**Why this is MASSIVE-ready:**
- **S3:** A 100MB app ZIP doesn't crash your Django server; it's streamed from S3
- **Celery:** Uploading doesn't freeze the page; worker unzips and generates preview in background
- **Bare Git Repos:** `git init --bare` per project is lightweight. You can host 10,000 repos on a 20GB disk
- **Cloudflare R2:** Cheaper than S3 for downloads (zero egress fees) — perfect for "download like git"

**Start Simple:** For first 500 apps, just use `FileField` (S3) + ZIP download. Git clone can be Phase 2. Don't over-engineer Day 1.

---

## 5. FULL APP FEATURES BREAKDOWN

### A. Publisher Flow (The Person with an Unpublished AI App)
1. Login -> **"Publish New App"**
2. Form: Title, Category (Landing/Dashboard/Stock/Full AI App), Tech Stack tags, AI-generated toggle, GitHub link (optional), ZIP upload
3. Django: Validates ZIP (no .env, no node_modules > 200MB), extracts `README.md` if exists, auto-generates thumbnail via Playwright screenshot
4. Creates `AppProject` + `AppVersion v1.0.0` + bare git repo: `mkdir -p /var/git/<user>/<slug>.git && git init --bare`
5. Success page: `Your app is live! Share: templatefolio.co.za/app/<slug> | Clone: git clone ...`

### B. Visitor Flow (Downloader)
1. Browse Gallery -> Filter: `Show: Snippets | Full Apps | AI Generated`
2. Card shows: `FULL APP • Django + React • 12 files • 4.2 MB • ★ 24`
3. Click -> App Page:
   - Top: Preview iframe (if snippet) OR Screenshot carousel + "Live Demo" button (if full app)
   - Right: `git clone` command with Copy button + `Download ZIP` (counts clones) + `Star` + `Tech Stack` badges
   - Middle: README.md rendered (like GitHub), File Browser (collapsible tree), Versions tab
   - Bottom: Comments

### C. AI Apps Special Handling
- Badge: `🤖 AI Generated with Lovable` — filterable
- Extra field: `Prompt used:` — so others can learn
- Warning: Run `pip audit` / `npm audit` in Celery worker and show "Security Check: 2 vulnerabilities" — builds trust

---

## 6. REVISED PROJECT STRUCTURE (Massive)

```
core/
gallery/         # Snippets + Full Apps (AppProject model)
  models.py
  views.py       # list, detail, upload, download, clone_counter
  git_utils.py   # create_bare_repo(), handle_push
  tasks.py       # Celery: process_zip_upload
users/           # Auth + Profiles (shows user's published apps like GitHub profile)
api/             # DRF for search, clone count
media/ -> S3
repos/           # /var/git/ (git bare repos) - NOT in Django media, separate volume
templates/gallery/
  app_list.html        # Now shows toggles: Snippets / Full Apps
  app_detail.html      # File tree + README + Clone box
  app_upload.html
```

---

## 7. UPDATED 10-WEEK TIMELINE (Massive Version)

| Week | Focus | Ship |
| :--- | :--- | :--- |
| **1-2** | Foundation (same as v1) | Gallery for snippets working, S3 setup |
| **3** | ZIP Upload MVP | `AppProject` model, upload ZIP form, S3 storage, Download ZIP, file browser (list files in ZIP), README render |
| **4** | Git Foundation | Create bare repos, show `git clone` URL, implement `git clone` counter (simple HTTP clone via `django-git` or just ZIP for now) |
| **5** | Massive Content | Upload 30 snippets + 10 full AI apps (generate 5 with v0/Lovable as dummy data) |
| **6** | Social | Auth, Stars, Clones, User Profile page `/u/thando` like GitHub |
| **7** | Search & Discovery | Postgres full-text on title/readme/tech_stack. Filters: `AI Generated`, `Tech: Django`, `Category: Track Stock` |
| **8** | Moderation & Safety | Admin queue for uploads, virus scan (ClamAV), max ZIP size 100MB, auto-reject node_modules |
| **9** | Performance | Cloudflare CDN, Pagination, S3 signed URLs for downloads, Celery workers |
| **10** | Launch Massive | Beta invite to 20 local devs in Durban to upload their unpublished apps |

**You can launch Layer 1 (snippets) at Week 3, then Layer 2 (full apps) is just an extension — no rewrite needed.**

---

## 8. COST & STORAGE FOR MASSIVE

- **S3 / R2:** 1,000 apps x 20MB avg = 20GB. R2 free tier 10GB, then $0.015/GB/mo = ~$0.15/mo for 20GB. Downloads via Cloudflare = free/cheap.
- **Server:** Render $7/mo Starter + $7 for Redis + S3 = ~$20/mo to host 1,000 apps.
- **Moderation is key:** Otherwise S3 bill explodes with junk. Require login + limit 5 uploads/day.

---

## 9. WHAT TO DO TOMORROW - MASSIVE EDITION

1. **Decide:** Do you want to start with **ZIP upload only** (fastest, covers 80% of "people with AI apps") or also **Git push** from Day 1? I recommend ZIP first.
2. **Say "Generate Massive Starter"** — I will rebuild your Django project in the workspace with:
   - New `AppProject` model (snippets + full apps)
   - Upload page (drag & drop ZIP)
   - Download + Clone counter
   - File browser for ZIPs
   - Seeded with 3 snippets + 2 fake full AI apps (so you can test download)
3. **Gather 2 unpublished apps:** Export one AI app as ZIP from Lovable/v0 to use as your first test upload.

---

## 10. VISUAL: HOW IT FEELS TO THE USER

> Like a mix of **GitHub** (clone, stars, README, file tree) + **Dribbble** (thumbnails, likes) + **Your Original Template Gallery** (copy button).

**Example App Card (Full App):**
```
[ Screenshot ]
FULL APP • AI Generated 🤖
AI Stock Tracker — Django + Tailwind
by @thando • Django • Tailwind • Chart.js
★ 42  •  ⬇ 128 clones  •  18 files
[ View Files ] [ Download ZIP ]
```

**Example Clone Box (Detail Page):**
```
Clone with Git
[ git clone https://templatefolio.co.za/git/thando/stock-tracker.git  [Copy] ]
or Download ZIP [⬇ 12.4 MB]
```

You now have a plan that can start small (v1 gallery) but is architected to become a **massive, Git-like hub for every unpublished AI app sitting on people's laptops.**

Want me to generate the massive Django starter? Reply **"Generate Massive Starter"**.
