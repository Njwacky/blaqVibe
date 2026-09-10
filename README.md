# BlaqVibes

**GitHub stores the code. BlaqVibes tells the story of what you're building.**

BlaqVibes is a creator network for builders and their projects: **BUILD → SHOW → REMIX → COMPETE → REPEAT**.

Publish what you made, get feedback, discover creators, remix ideas, take on challenges, and build a reputation around your work. You can build by hand, use AI as a tool, or remix another project — the platform does not treat any one creation method as the identity of the project.

## The core loop

1. **Build** something.
2. **Show** the working project, README, progress and trust status.
3. **Remix** another creator's idea or let someone remix yours.
4. **Compete** through challenges, battles, stars and reputation.
5. **Repeat** with BlaqVibes Today showing what happened and what to do next.

## Five places, on purpose

The primary navigation is **Projects · Discover · Skills · Challenges · Build**, and nothing else. Everything that is a utility — Saved, Inbox, Battle, Launch guides, Nolo, Trades, Sales, Settings, Admin — lives in the account menu, because the first thing anyone should see is what people are building.

- **Projects** (`/`) — the feed of what builders published.
- **Discover** (`/discover/`) — what is happening: most remixed ideas, fastest-growing remix families, top remixers, builders gaining momentum.
- **Skills** (`/skills/`) — Builder Skills: learn how other builders solve problems, with published projects as proof.
- **Challenges** (`/challenges/`) — a concrete reason to build today.
- **Build** (`/build/`) — start from scratch, remix a project, or use a Builder Skill; then CREATE → UPLOAD → SHOW → FEEDBACK → IMPROVE → PUBLISH.

## Arriving with nothing: import a GitHub repo

`/import/github/` (also on `/start/`) exists for the person who has no ZIP, no
editor open and no idea what to publish first. GitHub already publishes a ZIP of
every public repository at
`https://codeload.github.com/<owner>/<repo>/zip/refs/heads/<ref>`, so the
platform fetches that instead of asking anyone to build one.

One request does the whole thing: fetch → normalize → form-validate → save →
scan queue. It is deliberately **not** a second upload path — it hands the
project to the same `register_zip_project()` pipeline a hand upload uses, so
tree, scan, classification and trust all apply identically.

Two things make GitHub's raw archive unuploadable as-is, and both are handled:

1. **The wrapper folder.** GitHub nests everything under `blaqVibe-master/`.
   That one level is stripped, and only when *every* entry sits under it — a
   repository whose real content is `src/…` plus a root `README.md` keeps its
   tree intact.
2. **Paths the validator refuses.** This repository's own archive fails
   `validate_zip` on `scripts/ci.sh`, because `.sh` is a blocked extension.
   Refused paths are dropped and **reported to the user** — never removed
   quietly. `.env.example` with empty values comes through; `.env` does not.

The normalizer asks `validators.blocked_reason()` what to drop, so the importer
and the upload form can never disagree about what is allowed.

Hard limits, all enforced:

- **Host is pinned to `codeload.github.com`.** The URL is rebuilt from parsed
  `owner`/`repo`/`ref` rather than derived from the pasted string, so SSRF is
  structurally impossible — `http://169.254.169.254/…` never produces a request.
- 60 MB of archive on the wire (checked against `Content-Length` *and* while
  streaming, so a lying header does not make the cap optional), then the usual
  1000 files / 200 MB uncompressed / 50 MB per file.
- Connect + read timeouts, no redirects, `stream=True`.
- Login required, 5 imports/hour/user, same ceiling as publishing.
- Imported vibes are free (`0 ★`, `R0`): the same bytes are one click away on
  GitHub, so charging for them would be a paywall on nothing.

The import lands as **Pending Scan** like every upload. Importing is not a
shortcut past the checks.

The form is pre-filled with this repository, so the demo works on a fresh
database with no seeded content. `gallery/fixtures/github_blaqvibe_master.zip`
is a committed slice of the real archive — wrapper folder plus the offending
`ci.sh` — so the tests reproduce the exact failure with no network.

## AI is a tool, not a disguise

BlaqVibes does not try to make AI-built projects look human-built. If AI materially helped create a project, the publisher can mark it as AI-assisted and provide the tool and a short creation note. That provenance is part of the project's story.

The goal is simple: **make projects easier to trust, not harder to identify**.

A project can be:

- **Human-built** — created without material AI assistance.
- **AI-assisted** — a person used AI during development and remains responsible for the published work.
- **AI-generated** — substantially produced from an AI workflow.
- **Remixed** — derived from another BlaqVibes project, with the original lineage preserved.

AI labels are not quality scores. Trust comes from evidence: the files, README, runnable preview when available, security scanning, project history, creator identity, reviews and remix lineage.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
DEBUG=1 python manage.py migrate
DEBUG=1 python manage.py seed_demo
DEBUG=1 python manage.py runserver 0.0.0.0:8000
```

`DEBUG` defaults to **off** (fail-closed). Set `DEBUG=1` for local development so `runserver` serves static/media. Production must keep `DEBUG=0` and set a real `SECRET_KEY`.

## Demo Content

`seed_demo_content` creates an honest showcase catalogue for first-time visitors: five clearly labelled demo creator profiles, fourteen sample projects, four challenge prompts, and seven Builder Skills. The profiles use stable `demo_` usernames and say **Demo profile** in their bios; project cards and profiles display a **BLAQVIBES DEMO** badge. This content is sample work created by BlaqVibes, not user activity or testimonials.

The command creates no likes, comments, follows, messages, sales, stars, or fake statistics. Demo accounts have unusable passwords, so the seed cannot create public logins. It is safe to run repeatedly and never deletes data or overwrites a real account/project with a colliding identifier.

```bash
python manage.py migrate
python manage.py seed_demo_content
```

For Render Free, run it once as part of a temporary Start Command after migrations, then restore the normal web-server command. For example:

```bash
python manage.py migrate && python manage.py seed_demo_content && gunicorn blaqvibes.wsgi:application --bind 0.0.0.0:8000 --workers 3
```

Do not run this command automatically on every request. It is opt-in and does not add a public seed URL. To remove showcase content, review the stable `demo_` usernames, `demo-` project slugs, `demo-` skill slugs and challenge tags in the database, then delete only those records through an authenticated admin/database maintenance procedure; the command itself never deletes anything.

The older `seed_demo` command remains a local development fixture for the existing starter catalogue. Use `seed_demo_content` for the production-safe showcase content described here.

The demo seed is intentionally blocked on public/production-style hosts. **Demo passwords are never documented in this repository**; use the environment variables and local provisioning commands described below.

## Admin provisioning

There is no built-in production admin password. Create the operator account explicitly:

```bash
python manage.py create_superadmin --email you@domain --password 'A-strong-pass'
```

Keep that password out of source control, screenshots, documentation and chat logs. Use a secret manager or environment variable for hosted deployments.

### Render Free (no shell)

Render Free does not require an interactive shell for this. The repository includes a guarded, non-interactive command specifically for this situation:

```bash
python manage.py bootstrap_admin
```

Set these **Environment Variables** on the Render service:

- `BOOTSTRAP_ADMIN=true`
- `OWNER_USERNAME=<your username>`
- `OWNER_EMAIL=<your email>`
- `OWNER_PASSWORD=<a strong password>`

Do not put the password in GitHub or in the Start Command.

Then temporarily change the Render **Start Command** to run the bootstrap before the normal server command. For the Docker deployment in this repository, the equivalent is:

```bash
python manage.py migrate && python manage.py bootstrap_admin && gunicorn blaqvibes.wsgi:application --bind 0.0.0.0:8000 --workers 3
```

Deploy once. The command creates a new account or promotes an existing account with the same username to `is_staff=True`, `is_superuser=True`, `profile.role=superadmin`, and verified operator email. Existing accounts keep their current password; the bootstrap password is only used when a new account is created.

After the deployment succeeds and you can sign in at `/admin/`, **remove all four bootstrap environment variables and restore the normal Start Command**. With `BOOTSTRAP_ADMIN` absent, the command is a no-op.

This flow deliberately has no public "make me admin" URL, so an attacker cannot promote an account through the website.

Before exposing a deployment:

```bash
python manage.py security_check
python manage.py security_check --as-production
```

## Footer contacts are data, not a template

The public footer's **Contact** column is maintained at `/admin/footer-contacts/`
(admins and super admins only). It is a list of rows, not three fixed fields:

| Column | What it is |
| --- | --- |
| **Type** | Email, Phone, WhatsApp, X (Twitter), GitHub, Instagram, LinkedIn, Telegram, Discord, YouTube, TikTok, Website or Other link |
| **Address / number / handle** | The value for that type, normalised on save — `082 555 0100` → `+27825550100`, `https://twitter.com/blaqvibes` → the handle `blaqvibes` |
| **Display text** | Optional. What visitors read instead of the raw address, number or handle |
| **Order** | Lower numbers show first |
| **Show** | Untick to hide a method without deleting it |
| **Remove** | Delete a method that is gone for good |

Two support mailboxes, a WhatsApp line and an X account are four rows — no
migration, no deploy, no template edit. Blank rows are ignored, and the list is
cached for five minutes (dropped the moment a row changes).

The link is **built** from the value, never stored, so the footer can only ever
render `mailto:`, `tel:`, `wa.me`, the network named by the type, or a validated
`http(s)` URL: a pasted `javascript:` URL is rejected on save, and is ignored
even if a row bypasses the form.

## Tests and CI

Run the Django suite locally with:

```bash
DJANGO_LOCAL_DEV=1 DJANGO_TEST=1 python manage.py test gallery users
```

The repository CI equivalent is:

```bash
bash scripts/ci.sh
```

CI covers migrations, demo seeding, tests, security posture and feed smoke checks.

## What is real

- **Trust is evidence, not decoration.** Each project receives a pipeline-written trust tier (`verified`, `scanned`, or unknown). Content changes reset the tier until the new bytes are checked.
- **Dependency safety is real.** Manifest dependencies are checked against the npm/PyPI registries to reduce AI-generated slopsquatting risk. Registry failures fail closed rather than pretending a package is malicious.
- **Program kinds are explicit.** Projects can be games, APIs, mobile apps, notebooks, CLI tools and more. The platform says when a live preview is unavailable instead of faking one.
- **Discovery is personalised carefully.** The For You feed can learn from actions such as opening, starring, forking and trading, while explicit filters and non-default sorts stay truthful.
- **Git is real.** Smart HTTP supports clone/push flows subject to the same access, ownership and scanning rules as the web UI. A pushed project re-enters the scan queue.
- **Stars are an in-app ledger.** Stars can be earned through supported BlaqVibes activity and spent to unlock projects or support creators. They are not a promise of cash redemption or creator repayment.
- **Paid downloads are protected.** ZIPs, forks, Git URLs and media paths cannot bypass the access checks.
- **Paystack checkout is gated.** Buy flows only appear when the required payment configuration is present and webhooks are verified. Paystack is used for customer purchases, not creator cash-outs.
- **There is no creator cash-out program.** BlaqVibes does not promise to convert stars into ZAR, reimburse creators for stars, or transfer creator earnings to bank accounts. Stars stay inside the platform's economy.
- **Battle votes do not inflate project stars.** Competitive voting and creator popularity remain separate signals.
- **Remix lineage is preserved.** Forks keep a `forked_from` relationship so visitors can see where an idea came from and how it travelled. Discover turns that one column into real family statistics — remix depth, most remixed, fastest-growing families and top remixers — and never counts an unpublished remix.
- **Builder Skills carry immutable versions.** Editing a skill publishes a new version instead of rewriting the old one, and a project records the exact `source_skill_version` it was built from. “Built using Builder Skill X (v1)” still means v1 after the author rewrites the workflow.
- **Provenance is columns, not prose.** A project knows its creator, source project, source skill and skill version, build method, history and `published_at` (first publish, never moved).
- **Sales is activity, not income.** Projects sold, unlocks, views, conversion (withheld until it means something), purchases and popular projects. No earnings, no cash out, no creator balance.
- **BlaqVibes Today is the return loop.** It combines the daily mission, creator momentum, feedback, notifications and remixable work into one short command center.
- **AI tooling is honest.** Claude/Gemini/Groq are used only when configured; otherwise the built-in helper is presented as such. AI assistance is disclosed rather than hidden.
- **AI creation metadata is validated.** A publisher who marks a project as AI-assisted must name the tool and provide a short creation/workflow note. This makes the origin legible instead of turning AI into a mystery badge.
- **Nolo is an assistant, not the author.** Nolo can compare, explain and help with project material, but it does not turn BlaqVibes into an "AI app generator" identity.
- **Social sign-in is configurable.** Google, GitHub and Facebook providers require their own credentials in the environment.

## Stability and operations

- `/healthz` provides a lightweight liveness probe.
- `/readyz` checks database readiness and reports queue state.
- Structured logging is enabled by default; `LOG_LEVEL` controls verbosity.
- `python manage.py backup_db` creates consistent database snapshots and prunes old backups.
- Docker Compose healthchecks wait for healthy dependencies before starting dependent services.
- Production security is intentionally fail-closed: missing required secrets, unsafe debug posture and unsafe demo configuration can stop a deployment.

## Security note

Never commit passwords, API keys, webhook secrets, OAuth secrets, database credentials or other credentials. If a credential has ever appeared in a public Git history, treat it as compromised and rotate it even after removing the text from the latest README.

For the product architecture as shipped, read `docs/ARCHITECTURE.md` (source of truth). Older notes live in `docs/specs/`, `docs/demos/` and `docs/STABILITY.md`.