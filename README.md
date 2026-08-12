# BlaqVibes

Publish vibe-coded apps, trade stars, and remix other people’s work.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Tests:

```bash
python manage.py test gallery users
```

## What is real

- Paid ZIPs stay locked until a Trade or verified Sale. Fork, git URL, file preview, and `/media/apps/zips/` cannot skip that.
- Missing ClamAV does **not** auto-publish. The vibe stays pending for human review.
- Battle votes no longer inflate project stars.
- PR merge copies the fork ZIP + file list onto the target and re-queues a scan.
- Preview is an in-app page, not Docker.
- Inbox, saved vibes, email confirm, sitemap, and `/api/v1/apps/` exist.

Demos and old specs live in `docs/demos/` and `docs/specs/`.
