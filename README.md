# BlaqVibes

Publish vibe-coded apps, trade stars, and remix other people’s work.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
DEBUG=1 python manage.py migrate
DEBUG=1 python manage.py seed_demo
DEBUG=1 python manage.py runserver 0.0.0.0:8000
```

`DEBUG` defaults to **off** (fail-closed). Set `DEBUG=1` for local dev so
`runserver` serves static/media; in production `DEBUG` must stay `0` and
`SECRET_KEY` must be set (the app refuses to boot otherwise).

Local / CI auto-loads the demo catalog (`seed_demo`) so the first visit is
not an empty grid. Production stays empty until you run the command or set
`SEED_DEMO=1`.

Tests:

```bash
DJANGO_LOCAL_DEV=1 DJANGO_TEST=1 python manage.py test gallery users
```

CI runs migrate, `seed_demo`, and the same suite on every push and pull
request (`.github/workflows/ci.yml`).

## What is real

- **Preview files** is an in-app page (sandboxed snippet or file list + README). It is not Docker and not a live host.
- **Stars** are the complete money path: new users get 5 ★, a trade moves `star_cost` from buyer to seller atomically, and that Trade unlocks the ZIP.
- Paid ZIPs stay locked until a Trade or a verified Paystack Sale. Fork, git URL, file contents, and `/media/apps/zips/` cannot skip that.
- Card checkout is real Paystack initialize + signed webhook, and only if `PAYSTACK_SECRET_KEY` is set. The Buy button is hidden otherwise. Bank payouts to creators are **not** implemented.
- Missing ClamAV does **not** auto-publish. The vibe stays pending for human review.
- Battle votes no longer inflate project stars.
- PR merge copies the fork ZIP + file list onto the target and re-queues a scan.
- Inbox, saved vibes, email confirm, sitemap, and `/api/v1/apps/` exist.
- Google / GitHub / Facebook login: set client IDs in `.env` (see `docs/specs/SOCIAL_AUTH.md`).

Demos and old specs live in `docs/demos/` and `docs/specs/`.
