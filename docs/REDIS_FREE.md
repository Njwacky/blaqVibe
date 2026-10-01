# Redis-free uploads (Render + Supabase)

BlaqVibes no longer requires Redis. The default `SCAN_QUEUE_BACKEND=database`
stores scan jobs in the existing `gallery_scanjob` table. Production cache
entries and atomic rate-limit counters live in `blaqvibes_cache`, in the same
Postgres/Supabase database. Ordinary `python manage.py migrate` creates this
private cache table; no `createcachetable` command is needed.

## Render settings

Use these settings on the **web service**:

```text
SCAN_QUEUE_BACKEND=database
USE_REDIS=0
CELERY_EAGER=0
```

You can remove `REDIS_URL`. A leftover Redis URL does not enable Redis when
these defaults are used. `DATABASE_URL` (or `SUPABASE_URL`, containing the
Postgres connection string), `SECRET_KEY`, private object storage, mail,
HTTPS, and CSRF settings are still required as before. Do not turn off rate
limits or set production eager mode to compensate for a missing worker.

Deploy with migrations before starting Gunicorn. The repository's Docker
startup already does that. Keep the uploaded ZIPs in private object storage
(e.g. the configured Supabase Storage S3 endpoint), not Render's temporary
local media folder. This change does not migrate existing local files.

## What happens after an upload?

1. Authentication, rate limits, form/ZIP validation, storage and file indexing
   still run normally.
2. A durable `ScanJob(status='queued', task_id='')` is saved. No Redis connection
   is attempted and no slow scanner runs inside the upload request.
3. The upload page and owner notification say the files are saved/private and
   **waiting** for safety checks; they do not claim a scan is already running.
4. A separate runner performs the existing virus, secret and dependency checks
   and normal review/publication rules. Missing or failed checks never become
   a successful safety verdict just because Redis is absent.

New ZIPs, GitHub imports, ZIP edits, forks, PR merges and git pushes all use
the same dispatch function. Pending projects stay hidden from strangers and
do not appear on the public feed. Staff receive the existing review alert.

## Process scans without Redis

A database queue stores work; it does **not** execute work on its own. Render
Free's web service will accept uploads, but this code does not secretly launch
another process, scan inside HTTP requests, or make the service an always-on
worker. Until a runner is started, saved projects stay pending/private.

From a trusted machine or an external job runner with the **same deployed
code, database and private storage settings**, run one bounded batch:

```bash
python manage.py process_scan_queue --limit 20
```

For a standalone always-running worker (outside the web service):

```bash
python manage.py process_scan_queue --watch --limit 20 --poll-interval 5
```

Install ClamAV/signatures and the audit dependencies on the runner. The
repository's Dockerfile provides the scanner tooling; it no longer installs
redis-py by default. A local Docker example, using a private, Git-ignored `.env`
with the live database/storage configuration, is:

```bash
docker build -t blaqvibes-scanner .
docker run --rm --env-file .env blaqvibes-scanner \
  python manage.py process_scan_queue --limit 20
```

Do not put production keys into commands, Git, chat, a public workflow or an
HTTP "run scans" endpoint. Running a batch against the live database can
publish eligible, checked projects and send normal owner/staff notifications.
It does not execute uploaded bot/application scripts or deploy those apps.

The database claim prevents two runners from taking the same job. A replacement
upload invalidates a runner's claim. Evidence writes and finalization check
the claim and immutable archive name (or snippet content) before changing
the project, so old checks cannot overwrite a replacement's safety verdict. The runner does not
resurrect removed/quarantined projects or claim that unavailable ClamAV passed.
Nonzero scanner error exits are held for review rather than treated as clean.

### Previously failed uploads

Requeue old failed jobs, then process them. No second upload is needed:

```bash
python manage.py retry_failed_scans --limit 100
python manage.py process_scan_queue --limit 20
```

Stop old runners before using `retry_failed_scans --include-stale`. This
explicit recovery option also releases database claims older than 30 minutes,
as well as old unsent queued jobs. Sent Celery jobs are not reclaimed. It is
not an automatic timeout/retry guarantee.

## Health and maintenance

`/readyz` reports `checks.queue.backend='database'` and
`checks.queue.detail='database_queue'` when the queue table can be queried.
This confirms the durable queue is reachable, **not** that a runner is alive.
Monitor queue age and run a scanner regularly. The batch command exits nonzero
if a scan fails; the saved project remains private.

The default Compose deployment provides a `scan-worker` that polls the database.
Celery beat is no longer started by default; other periodic maintenance tasks
(daily/weekly challenges, ranking refreshes and retention work) need their own
scheduler if you use those features. Upload processing does not invent a new
scheduler for them.

Cache writes prune expired rows periodically but never evict live counters.
Counter increments are atomic across web workers. Cache payloads are signed
before serialization is decoded; the cache migration enables PostgreSQL RLS
without public policies so Supabase API roles cannot read its private entries.
Django must use the trusted database-owner connection, not an anonymous API key.

## Optional Celery later

Redis integration is retained only as an explicit opt-in:

```bash
pip install -r requirements-celery.txt
```

Set `SCAN_QUEUE_BACKEND=celery`, a working `REDIS_URL`, `CELERY_EAGER=0`, and
start a Celery worker. Set `USE_REDIS=1` only if you also want Redis-backed
caches. The Docker build flag is `INSTALL_CELERY_REDIS=1`; the optional Compose
overlay is `docker-compose.celery.yml`. Do not operate both types of scan runner
on the same queue. The database runner refuses to run in Celery mode.

**Unrelated issue:** removing Redis does not change Render/Cloudflare WAF rules
and will not unblock an HTTP upload rejected at the hosting firewall.
