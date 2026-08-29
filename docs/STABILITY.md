# BlaqVibes — Stability & Operations Guide

How BlaqVibes helps people, and what keeps it standing when real users arrive.

---

## 1. How the app helps people

BlaqVibes is a **marketplace + community for vibe-coded (and hand-written) software**.

| Who | What they get out of it |
| --- | --- |
| **Creators (low/no-budget founders)** | A free, on-brand way to publish any kind of program — web app, API, game, notebook, CLI — prove it is real (live preview or an honest "no live preview" badge), get stars, and **monetize with stars or real money (Paystack)** without owning a server. |
| **Buyers / users** | A catalog with **real trust signals** (virus + secret scans, dependency checks, git-auditable provenance), free clones of free vibes, and paid access that only unlocks after a real atomic trade/sale. |
| **Developers** | Real git hosting (`/git/…/….git` clone/push), forks, pull requests, and a `launch/` router that tells them **where** to deploy each artifact — with honest "this is not hosting" boundaries. |
| **The community** | A feed that learns your taste, weekly challenges, battles (without fake star inflation), reviews, follows, an inbox, and an AI assistant (Nolo) that is honest about whether a real LLM is behind it. |
| **Operators** | Append-only event logs power real dashboards, a `security_check` gate, maintenance mode, and role-gated moderation with a real audit trail. |

The trust system is the differentiator: a verified badge is written **only** by the scan pipeline and is reset when bytes change — a ✓ can never vouch for files nobody checked.

---

## 2. What "stable" means here

Stability is not one feature. It is the loop:

```
something breaks  →  we notice (health probes, logs, CI, Sentry)
        ↑                        ↓
   we recover          we find the cause (logs, backups, dashboards)
        ↑                        ↓
   we restore           we prevent recurrence (tests, checks)
```

Every item below closes one side of that loop.

---

## 3. Already shipped in this repo (stability work)

### 3.1 Ops probes — `/healthz` and `/readyz`
*(new: `gallery/health.py`, wired in `gallery/urls.py`)*

- `/healthz` — **liveness**. Always 200 while the process is alive. Touches no DB/cache/broker, so it cannot be the thing that is broken.
- `/readyz` — **readiness**. Runs `SELECT 1` against the DB (503 + JSON detail if it fails), and reports queue/broker state without gating on it (reads still work while Redis is down, and the Celery container has its own probe).
- Both are unauthenticated, JSON, `Cache-Control: no-store`, and **bypass maintenance mode** so a load balancer never sees "dead" during a planned window.
- Wire them into your load balancer / uptime monitor: `GET /healthz` for "is the box up", `GET /readyz` for "can it serve".

### 3.2 Fixed: maintenance mode never actually worked (caught by the new tests)
`MaintenanceModeMiddleware` ran **before** `AuthenticationMiddleware` in `MIDDLEWARE`. The moment maintenance was switched on, `request.user` raised, the silent `except Exception: pass` swallowed it, and the site kept serving 200s instead of the promised 503. It is now ordered after auth (with a `getattr` guard), and the superadmin bypass works. See `blaqvibes/settings.py` for the 5-Whys comment.

### 3.3 Structured logging
*(new: `LOGGING` block in `blaqvibes/settings.py`)*

- Console (stdout) logs — exactly what Docker/K8s collect; no file volume needed.
- `django.request` at WARNING (500s + stack traces surface, 404 noise stays quiet).
- App loggers `gallery`, `users`, `celery` at INFO, `LOG_LEVEL=DEBUG` for a bad night.
- Duplicate log lines prevented (`propagate=False`), Sentry integration untouched.

### 3.4 CI that actually runs
*(new: `.github/workflows/ci.yml`)*

Runs on every push/PR: migrate → seed → **all gallery+users tests** → `security_check` in production posture → asserts `seed_demo` refuses to run on a public posture → verifies `/healthz` and `/readyz` respond. 5-minute feedback instead of "it worked on my machine".

### 3.5 Docker Compose hardening
*(new: `docker-compose.yml`)*

- `restart: unless-stopped` on every service — a crash cannot take the stack down until a human notices.
- Healthchecks: Postgres (`pg_isready`), Redis (`redis-cli ping`), web (`/healthz` via urllib), Celery worker + beat (broker ping via redis-py).
- `depends_on` now waits for **healthy** Redis/DB (not just "started"), so gunicorn and workers never boot into a dead broker.
- `stop_grace_period` so Celery can drain in-flight scans on shutdown.
- Gunicorn access/error logs go to stdout.

### 3.6 Database backups
*(new: `gallery/management/commands/backup_db.py`)*

```bash
# SQLite (file-backed): consistent online snapshot via the SQLite backup API
python manage.py backup_db

# Postgres: pg_dump custom format (requires postgresql-client in the image)
python manage.py backup_db                 # backups/postgres-<stamp>.dump
python manage.py backup_db --keep 30       # prune all but the 30 newest

# Restore (human decision — restore is deliberately not automated):
#   SQLite:  sqlite3 db.sqlite3 < backup.sqlite3
#   Postgres: pg_restore --clean --if-exists -d blaqvibes backups/postgres-<stamp>.dump
```

Fail-closed design: it **refuses** in-memory databases (a backup that hangs/corrupts is worse than none) and errors loudly when `pg_dump` is missing. Cron example:

```cron
0 3 * * * cd /srv/blaqvibes && /usr/bin/python manage.py backup_db --keep 14 >> /var/log/blaqvibes-backup.log 2>&1
```

---

## 4. Runbook (what to do when things break)

| Symptom | First thing to check |
| --- | --- |
| "Site is slow / alerts on readyz" | `curl -s localhost:8000/readyz` → DB or queue detail. Check DB connections, Redis memory. |
| Load balancer restarts web | `curl -s localhost:8000/healthz`; if it fails the process is dead — read `docker compose logs web`. |
| Uploads never finish scanning | `docker compose logs celery` — the scan queue job exists? Redis reachable? ClamAV installed? |
| Bad deploy | `bash scripts/ci.sh` locally first. CI already gates migrate/seed/tests/hardening on PR. |
| I changed a model | New migration + run the full suite; CI does not merge red. |
| Accidentally broke data | `python manage.py backup_db` nightly; restore from `backups/`. Then find why (append-only logs, admin dashboard). |
| Need to take the site down | Superadmin → `/admin/dashboard/` → maintenance ON. /healthz and /readyz stay alive for monitoring. |

---

## 5. Recommended next steps (priority order)

1. **Real monitoring for the probes** — Uptime Kuma / Grafana / Pingdom: 5 min check on `/readyz`, alert on 503. This is the cheapest instability insurance.
2. **Object storage + Postgres in prod** — `DATABASE_URL` to managed Postgres, R2/S3 keys set, so `backup_db` (or the provider's snapshot) covers real data. Currently backups of SQLite are the default.
3. **Scheduled backups off-box** — cron inside the container is fine; an off-box copy (R2 versioning, restic, S3 lifecycle) survives a lost server.
4. **Celery worker monitoring** — count `ScanJob` rows by status per hour; alert when `pending` age > threshold (abandoned jobs are the top silent failure in queue-based apps).
5. **Rate limiting + abuse defences on money paths** — `/publish`, trades, payouts already carry limits; add alerting when they trip (that is how farming attempts look).
6. **Load test the scan pipeline** — 10 concurrent 50 MB ZIP uploads is your realistic peak; the scan queue is the bottleneck by design (1 worker, 2 min time limit).
7. **Backup and restore drills** — a backup that has never been restored is a hope, not a plan. Do one restore in staging per quarter.

---

## 6. What to keep NOT doing

- Do not set `SEED_DEMO=1` on a public host; the seeder deliberately refuses to run without a dev posture, and CI asserts it stays that way.
- Do not add a public-read ACL to the media bucket — paid ZIPs live there.
- Do not move `MaintenanceModeMiddleware` back in front of `AuthenticationMiddleware`.
- Do not fake health checks: `/healthz` never probes the DB on purpose. Liveness and readiness are different questions.
