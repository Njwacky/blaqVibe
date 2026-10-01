> **Current default (October 2026):** Redis is no longer required. Use the
> database queue and `process_scan_queue` described in [REDIS_FREE.md](REDIS_FREE.md).
> The broker-outage diagnosis below applies only to the optional
> `SCAN_QUEUE_BACKEND=celery` deployment. Saved failed jobs can be requeued with
> `retry_failed_scans` in either mode, without another upload.

# ZIP / GitHub upload failure diagnosis

Investigated 29 September 2026, starting from `f621ce4`.

## What was actually reproduced

**A valid ZIP returned Gunicorn’s bare “Internal Server Error” after 30.4 seconds.**
The failure was after the files and project had been saved, not while choosing a
ZIP and not during ZIP validation.

This is a confirmed failure in the checked-out application. The deployed
service’s logs and configuration were not available in this session, so it is
not proof that the same condition caused a particular live request. Use the
correlated logs below to confirm that without guessing.

## 1. Shared failure: queue dispatch → synchronous scan → worker timeout

Both entry points reach the same function:

```text
POST /publish/        ─┐
                      ├─ register_zip_project(project)
POST /import/github/ ─┘       │
                              ├─ project/file tree already saved
                              ├─ process_upload_pipeline.delay(project.id)
                              │    Redis connection/result-backend failure
                              └─ eager fallback inside the HTTP request
                                   scan_zip_with_clamav.apply(...).get()
                                     subprocess.run(clamscan, timeout=30)
                                       Gunicorn's 30-second worker timeout
                                         handle_abort → SystemExit(1)
                                         HTTP 500: Internal Server Error
```

The **original** failure locations in `f621ce4` were:

- `gallery/views.py:903`: Celery dispatch in `register_zip_project()`.
- `gallery/views.py:917`: the fallback runs ClamAV synchronously in the web worker.
- `gallery/tasks.py:165`: waiting for the ClamAV subprocess.

The request’s `except Exception` cannot catch Gunicorn’s `SystemExit`.
Dependency audits can also take up to 45 seconds each, so increasing the web
request timeout would merely hide the design problem.

### Controlled reproduction

Used the actual Gunicorn server with its default 30-second timeout, real Celery
Redis dispatch pointed at an unavailable local port, a valid small ZIP, and a
scanner stand-in that takes 27 seconds (below the application’s own scanner
limit). No production accounts or data were touched. SQLite/local development
settings were used for isolation; eager mode was explicitly disabled.

| Case | Before | After |
| --- | --- | --- |
| ZIP + unavailable broker + slow scanner | Bare HTTP 500 after **30.4 s**, worker exits | HTTP 302 in **0.246 s**; landing page 200, saved/private files, honest service-unavailable message |
| GitHub + unavailable broker | Same shared fallback in the source and regression test | HTTP 302 in **0.088 s**; landing page 200, saved/private files |
| GitHub object-store write refused | Unhandled HTTP 500 | HTTP 503 with the pasted link/title retained and a support reference |

GitHub archive fetching was stubbed with a valid GitHub-shaped archive in the
HTTP timing check; its network download is **not** included in the 0.088 s.
Neither fixed HTTP request invoked the scanner subprocess.

## 2. Separate GitHub failure: unhandled project/file save

The importer’s `try/except` protected `build_import()` (download and
normalization), but **not** the later `project.save()`.

An S3/R2 `AccessDenied`, missing bucket, disk failure, or SQL insert error could
therefore escape after a successful GitHub download. Reproduced using a
pathless object-store stand-in that raises a boto3 `ClientError` on write.

Both upload views now protect the save with a database savepoint, log
`stage=save_project event=failed`, and return a useful 503 instead of exposing a
traceback. The savepoint matters: catching an `IntegrityError` without rolling
back first can cause the error page’s own database queries to fail too.

The older **closed ZIP stream** bug is already fixed in the starting commit.
Its existing remote-storage tests still pass; it should not be assumed to be
the cause of every subsequent 500.

## What changed

- **No eager scan fallback on broker failure.** Explicit eager mode still works
  for development. Production should use `CELERY_EAGER=0` and a real worker.
- **Bounded dispatch:** no publisher retries, no Redis result subscription for
  the master task, three-second socket/connection limits, and no hidden Kombu
  `default_channel` reconnect loop on the HTTP producer. Worker reconnection
  policy is not disabled.
- **Durable failure state:** retain the ZIP and pending/private project, mark
  `ScanJob` failed, notify the owner and fan out the existing staff review alert.
  The success/progress pages no longer pretend that an undispatched scan is
  running. A recovered scan still runs all normal safety checks.
- **Bulk file indexing:** replace one SQL insert per archive member with batches,
  avoiding hundreds of remote-database round trips during `build_tree`.
- **Preserve worker verdicts:** a fast/eager worker’s final job status is no longer
  overwritten by the HTTP request’s stale `scanning` status.
- **Correlated stages:** one generated reference per POST, an
  `X-Upload-Reference` response header, stage start/completion/failure lines,
  elapsed time, project ID, and server-side traceback. ZIP contents and
  credentials are not added to stage metadata or public error messages.

No database migration is required.

## Find the exact failure on the deployed service

Deploy the changes, repeat one upload, and copy its reference from the error or
warning message (or the POST response header in browser Network tools). Search
Render’s application logs, or locally:

```bash
docker compose logs web | grep 'upload ref=THE_REFERENCE'
```

Typical line:

```text
upload ref=… source=github stage=queue_scan event=failed project_id=42 elapsed_ms=…
```

Read the deepest failed stage and its following traceback:

| Stage | What it isolates |
| --- | --- |
| `parse_repository` | Pasted GitHub link/branch parsing |
| `fetch_archive` | GitHub archive request/stream |
| `normalize_archive` | ZIP rewriting and archive safety validation |
| `validate_form` | Submitted fields and ZIP validation |
| `save_project` | Model preprocessing, storage write, SQL INSERT |
| `save_relations` | Related form data |
| `build_tree` | Stored ZIP read and file-index persistence |
| `queue_scan` | Job creation and broker dispatch |
| `notify_owner` / `notify_staff` | Receipt, moderator fan-out, email work |
| `request` only | Read the traceback: auth/rate-limit/cache work can fail before the view’s inner stages |

A completed outer `request` after a failed `queue_scan` is intentional: the
files were saved and the request recovered safely. If a worker is forcibly
killed, the last stage’s `event=started` line identifies the operation it was
inside even if it could not finish logging.

Check `<your-site>/readyz` as well. `checks.queue.ok=false` means the broker is
unreachable. `queue.ok=true` proves the broker answers, **not** that a Celery
worker is consuming the `scan` queue. Verify that the deployed worker uses the
same broker and database and runs:

```bash
celery -A blaqvibes worker -Q scan --loglevel=info -c 2
```

Do not turn on production debug pages, publish credentials, or disable safety
checks to get past an infrastructure error.

## Recover uploads that were already saved

After restoring the broker and scan worker:

```bash
python manage.py retry_failed_scans --limit 100
```

For **pre-fix worker timeouts**, a saved project can have an old `queued` job
with no task ID. Recover those explicitly too:

```bash
python manage.py retry_failed_scans --include-stale --limit 100
```

The extra option only includes unsent queued rows older than five minutes.
Already-sent queued jobs are not included. Both modes skip published, removed,
quarantined and ZIP-less projects, stop on a still-unavailable broker, and never
create another vibe or publish without scanning. Recovery is an operator action,
not an automatic background retry promise.

## Verification

- 15 new regression tests cover both upload doors, remote-storage and real SQL
  insert failures, bounded queue dispatch, no synchronous fallback, retained
  files/private status, correct user-facing progress, bulk inserts, worker-status
  races, correlated diagnostics, and safe recovery of failed/stale jobs.
- Gunicorn HTTP reproduction verified the original 500 and the fixed behavior.
- Ruff, Django system checks, dependency checks, whitespace checks and
  `makemigrations --check --dry-run` passed.
- **149 tests passed serially:** the 134 upload/publish/review tests plus the
  cache-isolation sentinel and all 14 tests that failed in the parallel run.
- Full-suite attempt: **1,551 tests with `--parallel 2`, 14 failures**. These were
  cached UI/counter/provider tests, including the unmodified test runner’s own
  cache-isolation sentinel. The existing custom runner flushes the main result
  class, not Django’s parallel worker result class; its cache leaks between
  worker tests. All 14 failed cases passed in the serial rerun. The parallel
  full-suite result is **not green**, and no unrelated test-runner changes were
  made for this upload investigation.
