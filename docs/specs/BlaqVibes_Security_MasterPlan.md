# BlaqVibes — Master Plan v3.0 | Git-Like Hub for Unpublished Apps
### With Full Security Logic (Production-Grade)

**App Name:** **BlaqVibes**
**Tagline:** *Publish the Vibes. Clone the Culture.*
**Date:** 08 Aug 2026 | Durban, ZA
**Stack:** Django 5 + PostgreSQL + S3/R2 + Redis/Celery + Gitea (Git) + Cloudflare
**Compliance:** South Africa POPIA + GDPR

---

## 1. POSITIONING — WHY BLAQVIBES

**BlaqVibes** is not just a template gallery. It's the **home for the unpublished** — the thousands of apps people built with AI (Lovable, v0, Cursor, ChatGPT) or code that never saw an App Store. Like Git, but curated, secure, and culture-first.

**Two layers:**
1.  **Vibes (Snippets):** Copy-paste HTML/CSS — Landing, Dashboard, Track Stock — one click
2.  **Full Vibes (Apps):** Full downloadable projects — `Download ZIP` or `git clone https://blaqvibes.co.za/git/thando/stock-vibe.git`

Your old prototype `template-gallery-prototype.html` is now rebranded to **BlaqVibes Dark Mode** — black + violet + gold.

---

## 2. COMPLETE SECURITY LOGIC — 7 LAYERS

This is what makes BlaqVibes massive-ready and trustworthy. If people are downloading and running other people's AI code, security is EVERYTHING.

### LAYER 1: IDENTITY & ACCESS (Who can do what)

**A. Authentication**
- `django-allauth` + `django-axes` (brute-force protection)
- Mandatory **Email Verification** before upload
- **2FA Optional** (TOTP via `django-otp`) for publishers with >10 apps
- Passwords: `Argon2` hasher (Django default now), 12 char min, HaveIBeenPwned check via `django-pwned-passwords`
- Session: `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_AGE=86400` (1 day)

**B. Authorization (RBAC)**
```python
ROLE_CHOICES = [
  ('visitor', 'Can view/download/clone, copy snippets'),
  ('creator', 'Can publish apps, manage own repos'),
  ('moderator', 'Can approve/reject, hide apps'),
  ('admin', 'Full access'),
]
# In views:
@login_required
@user_passes_test(is_creator)
def upload_app(request): ...

# Object-level: Only owner can delete/update
if project.owner != request.user and not request.user.is_staff:
    raise PermissionDenied
```
- Git push: SSH key auth. Each user adds public key in `/settings/keys` -> stored, verified, added to `authorized_keys` for git user `git`

### LAYER 2: UPLOAD SECURITY — THE MOST CRITICAL (ZIP & GIT)

This is where most "Git-like" clones get hacked. BlaqVibes enforces:

**1. Pre-Upload Gate (Django Form Validation)**
```python
# gallery/validators.py
MAX_ZIP_SIZE = 100 * 1024 * 1024  # 100MB hard cap
MAX_FILES = 2000
BLOCKED_EXT = ['.exe','.dll','.so','.dylib','.sh','.bat']
BLOCKED_NAMES = ['node_modules','__pycache__','.git','.env','venv','.venv']

def validate_zip(file):
    if file.size > MAX_ZIP_SIZE: raise ValidationError("Max 100MB")
    if not file.name.endswith('.zip'): raise ValidationError("Only ZIP")
    # Zip Bomb check: check uncompressed size without extracting
    with zipfile.ZipFile(file) as z:
        if len(z.infolist()) > MAX_FILES: raise ValidationError("Too many files")
        total_uncompressed = sum(f.file_size for f in z.infolist())
        if total_uncompressed > 500 * 1024 * 1024: raise ValidationError("Zip bomb detected")
        for info in z.infolist():
            # Path Traversal: ../../../etc/passwd
            if '..' in info.filename or info.filename.startswith('/'):
                raise ValidationError("Invalid path in ZIP")
            if any(blocked in info.filename for blocked in BLOCKED_NAMES):
                raise ValidationError(f"Blocked folder/file: {info.filename}")
            if any(info.filename.endswith(ext) for ext in BLOCKED_EXT):
                raise ValidationError(f"Blocked file type: {info.filename}")
```

**2. Celery Worker — Deep Scan (Async, doesn't block UI)**
```python
# gallery/tasks.py
@shared_task
def process_upload(project_id):
    project = AppProject.objects.get(id=project_id)
    tmp_path = download_from_s3(project.zip_file)
    
    # a) ClamAV Virus Scan
    result = subprocess.run(['clamscan','--no-summary', tmp_path], capture_output=True)
    if result.returncode == 1: # Virus found
        project.status = 'quarantined'
        project.save()
        notify_admin(f"Virus in {project.slug}")
        return

    # b) Secrets Scan - detect .env, API keys, private keys
    secrets_found = scan_for_secrets(tmp_path) # regex for sk_live_, AKIA, -----BEGIN PRIVATE KEY
    if secrets_found:
        project.status = 'needs_review'
        project.secrets_warning = secrets_found # show to owner: "We found a possible Stripe key, remove it?"
    
    # c) Dependency Audit
    if os.path.exists('package.json'):
        audit = subprocess.run(['npm','audit','--json'], capture_output=True)
        # save audit results to project.audit_report

    # d) Generate safe file tree + README
    project.file_count = count_files(tmp_path)
    project.save()
```

**Workflow:** `Upload -> S3 (quarantined bucket) -> Celery Scan -> if clean -> move to public bucket -> status='published'`. Users see "Scanning... 12s" toast.

### LAYER 3: STORAGE & DOWNLOAD SECURITY

- **Two Buckets (S3/R2):**
  - `blaqvibes-quarantine` (private, no public access) — all fresh uploads
  - `blaqvibes-public` (private too, but served via Signed URLs) — only clean apps
- **Never serve direct S3 URL.** Django generates **Signed URL valid for 5 minutes:**
```python
# gallery/views.py
def download_zip(request, slug):
    project = get_object_or_404(AppProject, slug=slug)
    # Rate limit: 10 downloads / minute / IP
    if is_rate_limited(request): return HttpResponseTooManyRequests()
    project.clones = F('clones') + 1
    project.save()
    # Generate presigned URL
    url = s3.generate_presigned_url('get_object', Params={'Bucket':'blaqvibes-public','Key':project.zip_file.name}, ExpiresIn=300)
    return redirect(url)
```
- **Git Repos:** Stored outside web root `/var/git/` with permissions `750 git:git`. Django only stores `repo_path`, never serves files directly. Git HTTP handled by `git-http-backend` behind Nginx + auth check against Django session.

### LAYER 4: CODE PREVIEW & XSS ISOLATION

**Never render user HTML with `|safe` in main site.**
- **Snippet Preview:** Isolated `iframe` with `sandbox="allow-scripts allow-same-origin"` + separate domain if possible `preview.blaqvibes.co.za`
```html
<iframe src="{% url 'preview' project.slug %}" sandbox="allow-scripts" csp="default-src 'self' 'unsafe-inline'"></iframe>
```
- **Preview View:** Sets `Content-Security-Policy: default-src 'self'; script-src 'unsafe-inline';` and `X-Frame-Options: ALLOWALL` (only for preview)
- **README.md:** Rendered with `markdown` + `bleach` sanitizer (strip `<script>`, `onload=`)
```python
import bleach
clean_html = bleach.clean(markdown_html, tags=['p','h1','h2','a','code','pre'], strip=True)
```
- **File Browser:** Never execute files, just display as text with `highlight.js`

### LAYER 5: WEB & INFRASTRUCTURE SECURITY

**Django settings.py (Production)**
```python
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY' # except preview
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = ['https://blaqvibes.co.za','https://*.blaqvibes.co.za']
# Rate Limit
REST_FRAMEWORK = {'DEFAULT_THROTTLE_RATES': {'upload':'5/hour','download':'30/minute'}}
```

- **Rate Limiting:** `django-ratelimit` on `upload_app` (5/hour), `download_zip` (30/min/IP), `login` (5/min)
- **DDoS:** Cloudflare (free) in front of all traffic — enable "Under Attack Mode" for `/git/` endpoints
- **Secrets:** Use `.env` + `django-environ`, never commit. In prod, use Render Env Vars or Doppler
- **HTTPS Everywhere:** Let's Encrypt via Render / Cloudflare

### LAYER 6: CONTENT MODERATION & TRUST

- **Auto-Mod Queue:** Every new Full App goes to `status='pending_review'` for first 3 uploads of a new user. Moderator approves in Django Admin (1-click).
- **User Reports:** `Report App` button -> `AppReport` model (spam, malware, copyright)
- **AI Content Label:** `ai_generated` tag + prompt disclosure builds trust; no penalty
- **Copyright:** DMCA takedown form + `hash` check (SHA256) to prevent re-upload of removed apps
- **Reputation:** Users with 3 rejected uploads -> auto shadow-ban from publishing (need manual review)

### LAYER 7: DATA PROTECTION (POPIA - ZA) & LOGGING

- **POPIA:** Minimal data: email, username, IP. No ID numbers. Privacy Policy states purpose. Users can `Delete Account` -> hard delete via `GDPR` view
- **Logging:** `django-auditlog` for all admin changes + upload logs: `user_id, ip, file_hash, timestamp`
- **Monitoring:** Sentry for errors, UptimeRobot for uptime, `django-admin-honeypot` for fake admin traps
- **Backups:** Daily PostgreSQL + S3 versioning enabled (recover deleted ZIPs). Test restore monthly

---

## 3. UPDATED SITEMAP FOR BLAQVIBES

```
/                                   # Vibes Feed -Toggle: Snippets | Full Apps
/app/<slug>/                        # App Detail - README + Files + Clone Box
/app/<slug>/preview/                # Isolated Preview (snippet only)
/app/<slug>/download/               # Signed S3 redirect (logs clone)
/u/<username>/                      # Creator Profile - like GitHub
/publish/                           # Upload ZIP + Create (rate limited)
/publish/git/                       # Git SSH keys + clone URL instructions
/moderation/queue/                  # Staff only
/api/v1/apps/?q=&tech=django        # Public API
/admin/                             # Hardened: honeypot at /admin/ decoy
```

---

## 4. BLAQVIBES BRANDING & UI

- **Palette:** Black `#0A0A0F` + Violet `#7C3AED` + Gold `#F59E0B` + White
- **Logo:** ◈ BlaqVibes (geometric)
- **Voice:** Culture, not corporate. "Drop your vibe", "Clone the culture"

---

## 5. NEXT STEPS — BUILD BLAQVIBES

**Option A — I generate the secure starter NOW (Recommended):**
Reply **"Generate BlaqVibes Starter"** and I will create in your workspace:
- Django project `blaqvibes/` with `AppProject` model (snippets + full apps + security fields)
- `validators.py` (zip bomb, path traversal, blocked ext)
- `tasks.py` (Celery skeleton for ClamAV + secrets scan)
- Upload form with rate limit + S3 signed URL views
- Hardened `settings.py` (HSTS, CSP, Argon2, Axes)
- Seeded with 3 snippets + 1 fake full app + admin user

**Option B — You want the Figma first:**
I can generate the BlaqVibes landing page + app detail UI mockup as HTML.

What do you want to do? Also, should git clone be via **HTTPS only** (easier) or **SSH keys** (like real Git) for BlaqVibes?

