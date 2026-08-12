# BlaqVibes App Overview

This project is a Django-based marketplace and community platform for sharing, reviewing, trading, and running code “vibes” or app projects. In practice, the app lets users upload ZIP packages of web apps or snippets, have them scanned for malware/secrets, publish them publicly, browse the feed, preview files, download them, star or trade them, and interact with community features like comments, reviews, challenges, and battles.

This is not just a gallery. It is a full social + marketplace + publishing workflow around uploaded app projects.

---

## 1. What the app is

The core product revolves around `AppProject` records in the `gallery` app.

Each project includes:
- title, slug, owner
- category and tags
- short description and README
- HTML/CSS/JS source code
- optional uploaded ZIP file
- status: pending / published / quarantined
- stats: views, clones, copies, stars, avg rating
- file tree and language stats
- AI metadata and scan metadata

The app behaves like a “code marketplace + app showcase” where users can:
- publish a project
- showcase a live preview
- let others inspect files
- download or clone the ZIP
- star it or trade stars for access
- review it and comment on it
- participate in weekly challenges and battles

---

## 2. High-level architecture

### Django project
The project root is `blaqvibes/` and contains the main Django settings and URL config:
- `blaqvibes/settings.py` sets the app, database, security, Celery, Cloudflare R2/S3, and local dev behavior
- `blaqvibes/urls.py` routes the main app and admin URLs

### Main app modules
- `gallery/` = core product logic: projects, feeds, upload/publish, moderation, scans, trading, battles, AI features
- `users/` = profiles, site settings, roles, follows, admin logs

### Main model groups
The main models live in `gallery/models.py` and include:
- `Category` and `Tag`
- `AppProject`
- `AppFile`
- `Star`, `Comment`, `Review`
- `Trade`, `Sale`, `VibeView`
- `ScanJob`
- `VibeBattle`, `BattleVote`
- `Deploy`
- `Season`, `Challenge`, `PullRequest`, `AppVersion`

---

## 3. How the app works end-to-end

### A. User signs up and creates a profile
The app uses Django’s built-in auth and a custom `Profile` in `users/models.py`.

Profile features include:
- role: user, moderator, admin, superadmin
- stars balance
- pro plan
- auto language detection
- Nolo review toggle
- auto thumbnail and trading settings
- admin/security settings

A `post_save` signal automatically creates the profile for each new user.

### B. User uploads a project
The upload flow is handled by `gallery/views.py`:
- `publish()` renders the upload form
- the user submits a ZIP and metadata
- the app creates an `AppProject` as `pending`
- if there is a ZIP, it builds a file tree from the archive and stores `file_tree` and `file_count`
- it creates a `ScanJob` and triggers the async upload pipeline

This is the source of the platform’s trust/security model: every upload is scanned before it can be publicly published.

### C. Upload pipeline and security scan
The upload pipeline is implemented in `gallery/tasks.py`.

The queue flow is designed this way:
1. `scan_zip_with_clamav()`
   - checks the ZIP with `clamscan` if installed
   - scans for common secret patterns in zipped text files
   - marks the project as quarantined if malware or secrets are found
2. `vulnerability_scan()`
   - `npm audit` for JS projects
   - `pip-audit` for Python projects
   - Nolo review heuristics/AI review
3. `finalize_publish()`
   - sets published state when allowed
   - updates scan status
   - sends email notifications

The app uses Celery with `scan` queue and `CELERY_TASK_ALWAYS_EAGER` in local dev, so it still works without Redis.

### D. Feed and discovery
The homepage and catalog are handled by `feed()` in `gallery/views.py`.

Users can search and filter by:
- text query
- category
- kind (snippet vs full app)
- AI-generated flag
- tech stack keyword
- sort order

This is a social marketplace feed, not a static template library.

### E. Project detail page
`app_detail()` displays:
- project title, description, metadata
- live preview embed
- README content
- comments
- reviews
- star state
- view count
- ownership and profile metadata
- optional scan status and AI README preview

Only published projects are visible to normal users; owners can see their own pending or quarantined projects.

### F. Preview and file inspection
`preview()` serves an isolated preview page for each project.

The app also exposes:
- `snippet_css()` and `snippet_js()` for external CSS/JS file delivery
- `file_preview()` which reads a file from the ZIP and returns it as JSON if it is text and under 200KB

This allows users to inspect the content of uploaded files without downloading the entire archive.

### G. Download and clone behavior
`download_zip()` is a key conversion step:
- increments `clones`
- checks if S3 / Cloudflare R2 is enabled
- if enabled, redirects to a presigned signed URL
- otherwise streams the ZIP file directly

This is how the app balances security and storage cost while allowing users to get the actual app package.

### H. Community features
The app has a rich interactive layer:
- Comments via `post_comment()`
- Reviews via `post_review()`
- Stars via `toggle_star()`
- Trading via `Trade` and `Sale`
- Profile views via `VibeView`
- Follow system through `users/models.py`

This creates a social proof layer around uploaded code.

### I. Challenges, battles, and seasonal events
The app includes:
- `Challenge` model for weekly challenge campaigns
- `VibeBattle` and `BattleVote` for head-to-head comparison
- `Season` model for time-based competition

These are layered features that make the platform gamified and community-driven.

### J. Deploy and live-run features
There is also a deployment flow:
- `Deploy` model stores live URLs and tokens
- `deploy_view` is a route that serves or exposes a runable project URL
- the settings include `auto_run_enabled` for automatic live deployment after upload

This means a project can be uploaded and optionally launched as a temporary live app.

### K. AI features
The codebase includes AI-driven functionality:
- `gallery/ai_readme.py`
- `gallery/nolo_ai.py`
- `gallery/nolo_review.py`
- `gallery/challenge_ai.py`

These support:
- AI-generated README generation
- AI review summaries
- challenge generation
- README insertion and preview

### L. Security and moderation layer
The platform includes several safety layers:
- ClamAV scanning for malware
- secret pattern detection in project ZIP contents
- HTML sanitization and prompt sanitization
- CSRF and CSP protections
- rate limiting on upload/comment actions
- moderation queue and reporting system

This is one of the more sophisticated parts of the app; it is designed to prevent unsafe or malicious content from being published.

---

## 4. Main data model in plain English

The most important entity is `AppProject`.

An `AppProject` is a single uploaded app or snippet submitted by a user. It contains the source, metadata, status, and stats needed to display it in the marketplace and manage moderation.

Related items include:
- `Category` = section such as snippet/full app
- `Tag` = labels like AI, trading, dashboard, etc.
- `AppFile` = indexed files inside the uploaded ZIP
- `Review` = rating from 1 to 5
- `Comment` = discussion under the app
- `Trade` = star-based exchange between users
- `Sale` = paid purchase via Paystack
- `ScanJob` = queue state for security checks

---

## 5. Where the app is strongest

This app is strongest as a combination of:
- a code-sharing marketplace
- a curated social feed of “vibes”
- a security-gated upload pipeline
- a community and competition system
- a platform for downloadable and runnable apps

It is more than a template gallery because it includes:
- file-tree inspection
- project moderation
- asset and ZIP management
- signed download URLs
- economic features (stars, trades, sales)
- AI review and summary layers

---

## 6. Best existing markdown document

The closest existing document is [README_BlaqVibes.md](README_BlaqVibes.md). It is useful, but it reads more like a feature/update log than a complete architecture overview.

It does not cover the app end-to-end in one place:
- the actual data model
- the security workflow
- the upload pipeline
- the social marketplace behavior
- the challenge/battle/system design
- the app lifecycle from upload to publication to download

Because of that, I created this fuller overview: [BlaqVibes_App_Overview.md](BlaqVibes_App_Overview.md).

---

## 7. Quick start

From the project root:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

For background checking and publishing tasks, Celery is used:

```bash
celery -A blaqvibes worker -l info
```

The project’s default local settings are designed to work in dev mode without Redis by using eager task execution.

---

## 8. Bottom line

This app is a Django-powered platform for publishing and discovering code projects, with strong community, moderation, scanning, and marketplace features layered on top.

The real system is:
- upload app or snippet
- scan and validate it
- publish if safe
- display and interact in feed/detail pages
- enable downloads, reviews, stars, trades, and AI enhancements

That is the core behavior of BlaqVibes.
