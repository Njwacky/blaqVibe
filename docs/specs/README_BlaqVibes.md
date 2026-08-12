# BlaqVibes — Full Code (Updated with ClamAV + R2 Signed URLs)

## Hardening pass (2026-08-12)

Downloads, git clone, and file preview now require a real Trade or Sale when a vibe is priced. Paystack no longer grants access if checkout fails. Pro trials expire after 7 days. Viewer lists are owner-only. Battles need POST and award +1 star. Preview iframes are sandboxed. Auth has styled signup/login and password reset. Light/dark + mobile nav are in the Django app. Run `python manage.py test gallery users` to verify.


**5 Whys: No Shortcuts, Full Code**

## What's New (You said: Yeah add it)

### 1. S3 / Cloudflare R2 Signed URLs (300s)
- **Why presigned?** No public bucket, 5-min expiry, offload bandwidth to R2 (zero egress).
- **Code:** `gallery/storages.py:get_presigned_url()` uses `boto3` with `endpoint_url` (R2). `gallery/views.py:download_zip()` increments `clones` atomically, then `HttpResponseRedirect(presigned_url)` if `is_s3_enabled()`, else serves locally (dev fallback).
- **Config:** Set env vars from `.env.example`. Dev without keys still works (local `media/`).
- **Quarantine → Public:** Fresh ZIP goes to `blaqvibes-quarantine` bucket, after Celery clean it would be copied to `blaqvibes-public` (code in `tasks.py`).

### 2. ClamAV Celery Worker
- **Why async?** Scan 2-8s blocks request. Celery offloads.
- **Code:** `blaqvibes/celery.py` + `gallery/tasks.py:scan_zip_with_clamav(project_id)` — tries `subprocess.run(['clamscan', ...])`, fallback mock if not installed (dev), secrets regex scan, rebuilds file tree if missing, sets `status='quarantined'` or `'published'`.
- **Eager mode:** `CELERY_TASK_ALWAYS_EAGER=1` by default (dev, no Redis needed). Prod set `CELERY_EAGER=0` + `REDIS_URL`.
- **Trigger:** `gallery/views.py:publish()` calls `scan_zip_with_clamav.delay(project.id)` after tree build.

## Run

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000  # Eager mode, no Redis needed
# For real worker (prod):
# redis-server &
# celery -A blaqvibes worker -l info
```

## Test

1. Login `nolo.ai` / `blaq12345` → `/publish/` → upload ZIP → after 1s, detail shows tree + README.
2. Click Files → preview `stock_app/views.py` → 200KB limit.
3. `/app/stock-app-vibes/download/` → logs `clones +1`, redirects to presigned URL if S3, else serves ZIP.
4. Check logs: `celery` would show `ClamAV clean: stock-app-vibes` or mock.

## Env for Prod
Copy `.env.example` → `.env`, set R2 keys, `DEBUG=0`, `CELERY_EAGER=0`, `REDIS_URL`.

Live server: port 8000 (restarted below).
