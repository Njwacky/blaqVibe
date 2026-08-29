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

## Admin sign-in

There is **no** built-in `admin` password. `admin` is a reserved handle
(you cannot sign up as it), and `/admin/` is a honeypot — the real Django
admin lives at `/blaq-admin-secure/`. App admin is `/admin/dashboard/`
after you sign in at `/accounts/login/`.

`createsuperuser` is not enough: BlaqVibes gates admin pages on
`profile.role`, which that command leaves as `user`, so you sign in and
then get a 403.

The operator email is **`admin@blaqvibes.co.za`**. Provisioning marks it
verified up front (Profile + allauth EmailAddress) — there is no
confirmation link to click. Sign in with that email at `/accounts/login/`.

Local demo after `seed_demo` — **only** when `DEBUG=1` or `DJANGO_LOCAL_DEV=1`:

- Operator email (already confirmed): `admin@blaqvibes.co.za` — password from `DJANGO_SUPERADMIN_PASSWORD` / `create_superadmin`
- Superadmin: `nolo.ai` / `blaq12345`
- Admin: `blaq` / `blaq12345`
- Moderator: `thando` / `thando12345`

`python manage.py seed_demo` **refuses to run** on a public host (no `DEBUG`, no
`DJANGO_LOCAL_DEV=1`) because those passwords are in this file. A demo/staging
box that only wants the published catalog runs
`SEED_DEMO_FORCE=1 python manage.py seed_demo` — same vibes, accounts created
with unusable passwords and empty wallets. Never set `SEED_DEMO=1` on a host
that takes real uploads.

Create the operator account (required in production):

```bash
python manage.py create_superadmin --email you@domain --password 'A-strong-pass'
```

Or set `DJANGO_SUPERADMIN_PASSWORD` in `.env` — `migrate` / `seed_demo`
will create or repair `admin` with that password and set
`profile.role=superadmin`.

Before you expose anything:

```bash
python manage.py security_check          # ERRORs on an unhardened production config
python manage.py security_check --as-production   # same audit against a dev-configured process
```

`docker-compose.yml` runs `security_check` in the web container's boot command, so
a missing HSTS / dev `SECRET_KEY` / `SEED_DEMO=1` stops the deploy instead of
shipping quietly.

Tests:

```bash
DJANGO_LOCAL_DEV=1 DJANGO_TEST=1 python manage.py test gallery users
```

CI is `bash scripts/ci.sh` (migrate, `seed_demo`, tests, assert the feed
is not empty). To run that on GitHub Actions, copy `docs/ci-github-actions.yml`
to `.github/workflows/ci.yml` (needs the `workflows` permission).

## What is real

- **The trust badge is real evidence, not decoration.** Every vibe carries a pipeline-written tier — `verified` (virus clean, no leaked secrets, dependencies audited), `scanned` (pipeline ran, a check incomplete), or nothing (no complete evidence). Only `gallery.trust.apply_trust_grade` may write it: never a form, never the API, never a user. Changing the content (edit, git push, PR merge) resets the badge to unknown until the new bytes are re-scanned — a ✓ can never vouch for files nobody checked. The public standard lives at `/trust/`, the feed and API accept `?trust=verified`, and the tier adds a small (≤8%) ranking boost that reorders equals but cannot buy rank. See `docs/specs/BlaqVibes_Trust_Badge_Spec.md`.
- **The slopsquatting check is real.** AI-generated code invents package names and attackers register them with malware — so every manifest dependency is checked against the real npm/PyPI registry. Only an explicit 404 counts as "does not exist" (a registry hiccup can never smear a creator); flagged names cap the tier at `scanned` and say so on the detail page. Budgeted (120 registry calls/hour, env-tunable), cached 24h, capped at 20 names/project, kill switch `DEP_CHECK_ENABLED=0`. See `docs/specs/BlaqVibes_Dep_Check_Spec.md`.
- **Every kind of program is publishable** — games, APIs, mobile apps, notebooks, CLI tools. Each vibe is auto-labelled with a program kind (creator can override), and anything our sandbox cannot run says "no live preview" plainly instead of faking one. See `docs/specs/BlaqVibes_Discovery_Spec.md`.
- **The feed learns.** Open, star, fork, or trade a kind of program and it moves to the front of *your* grid ("For you"). Explicit filters and non-default sorts are never silently personalised. Anonymous visitors get the global interest ranking.
- **Preview files** is an in-app page (sandboxed snippet or file list + README). It is not Docker and not a live host.
- **Git is real.** `/git/<user>/<slug>.git` serves the smart-HTTP protocol (Dulwich): `git clone` follows the download rules (free vibes clone anonymously, paid vibes challenge with 401 until you present credentials with a Trade/Sale), and `git push` works for the owner/co-owners via Basic auth (password or a revocable git token from Settings). A pushed HEAD becomes the current ZIP and re-enters the scan queue — no scan bypass via git. Repos are rebuilt from the stored ZIPs as snapshot commits, so the cache is disposable.
- **The admin dashboard charts real events.** Clones/day, trades, star volume, signups, uploads, scan outcomes, top vibes and the quarantine rate are server-rendered SVGs drawn only from append-only logs (`CloneEvent`, `Trade`, `ScanJob`) — cumulative counters are never charted because they have no timestamps.
- **Stars** are the complete money path: new users get 5 ★, a trade moves `star_cost` from buyer to seller atomically, and that Trade unlocks the ZIP.
- **Usernames are PUBG-rule identity** (`users/rename.py`): you cannot rename any time you want — a rename card is free while Pro or costs 100 ★ (burned, ledgered `rename_spend`), one rename per 30 days, your old handle stays reserved 90 days, and old `/u/<oldname>/` links redirect to the new profile. The display name itself is stylable (font, color, size, anime shine/rainbow effects) — server-rendered from whitelisted slugs only, 20 ★ per change or free while Pro — so it shows off on feed cards, profiles, and follower lists without any user-supplied CSS.
- Paid ZIPs stay locked until a Trade or a verified Paystack Sale. Fork, git URL, file contents, and `/media/apps/zips/` cannot skip that.
- Card checkout is real Paystack initialize + signed webhook, and only if `PAYSTACK_SECRET_KEY` is set. The Buy button is hidden otherwise.
- **Creator cash-outs exist.** `/payout/` lets a verified creator hold stars (10 ★ = R1, min 500 ★) and request a bank payout; the hold is ledgered (`payout_hold`). A money admin approves/rejects at `/admin/payouts/` — rejection refunds the stars (`payout_refund`). With `PAYSTACK_SECRET_KEY` set the admin can start a real Paystack transfer (code recorded on the row); a human still flips it to *paid*, because a pending transfer is not money in the bank.
- Missing ClamAV does **not** auto-publish. The vibe stays pending for human review.
- Battle votes no longer inflate project stars.
- PR merge copies the fork ZIP + file list onto the target and re-queues a scan.
- Inbox, saved vibes, email confirm, sitemap, and `/api/v1/apps/` exist.
- Nolo chat uses Claude if `ANTHROPIC_API_KEY` is set, else Gemini/Groq, else a short built-in helper. It never pretends to be a live model without a key.
- **Nolo prompts are token-cheap by default.** `gallery/prompt_economy.py` is a real prompt engine, not a toggle: stable system instructions go first, dynamic user text goes last, whitespace/duplicate-prose is compressed, inputs hit a hard character budget (code is cut at a line boundary), and a provider prompt-cache hint is sent only when the stable prefix is large enough to be honoured. `/nolo/chat/send/` returns a `meta` block with before/after token estimates so the saving is observable, not claimed.
- Google / GitHub / Facebook sign-in: set each provider's client id **and** secret in `.env` (see `docs/specs/SOCIAL_AUTH.md`).

## Stability & ops

- **Probes:** `/healthz` (liveness — always 200 if the process is alive, touches nothing) and `/readyz` (readiness — `SELECT 1` against the DB, reports queue state; 503 with JSON when the DB is unreachable). Both bypass maintenance mode. See `docs/STABILITY.md`.
- **Logging:** structured console logging is configured by default (`LOG_LEVEL` env to change verbosity). 500s + stack traces surface via `django.request`; app loggers (`gallery`, `users`, `celery`) are at INFO.
- **Backups:** `python manage.py backup_db` writes a consistent snapshot to `backups/` (SQLite online backup API; Postgres `pg_dump -Fc`). Refuses in-memory DBs and prunes to `--keep` (default 10).
- **CI:** `.github/workflows/ci.yml` runs migrate → seed → all tests → `security_check` in production posture → probe smoke test on push/PR. Local equivalent: `bash scripts/ci.sh`.
- **Compose:** every service has `restart: unless-stopped`, healthchecks (DB, Redis, web, Celery worker/beat), and `depends_on` waits for *healthy* dependencies. Override defaults in hosted Docker: map `8000:8000`, set `DATABASE_URL`/`REDIS_URL`.
- **Known-good posture:** `DEBUG=0`, `SECRET_KEY` set (app refuses to boot otherwise), `RATELIMIT_ENABLE=1`, maintenance mode behind `MaintenanceModeMiddleware`. `python manage.py security_check` gates deploys via the compose boot command.

Demos and old specs live in `docs/demos/` and `docs/specs/`.
