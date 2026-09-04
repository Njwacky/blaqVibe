# BlaqVibes

**GitHub stores the code. BlaqVibes tells the story of what you're building.**

BlaqVibes is a creator network for vibe-coded apps and experiments: **BUILD → SHOW → REMIX → COMPETE → REPEAT**.

Publish what you made, get feedback, discover creators, remix ideas, take on challenges, and build a reputation around your work.

## The core loop

1. **Build** something.
2. **Show** the working project, README, progress and trust status.
3. **Remix** another creator's idea or let someone remix yours.
4. **Compete** through challenges, battles, stars and reputation.
5. **Repeat** with BlaqVibes Today showing what happened and what to do next.

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

The demo seed is intentionally blocked on public/production-style hosts. **Demo passwords are never documented in this repository**; use the environment variables and local provisioning commands described below.

## Admin provisioning

There is no built-in production admin password. Create the operator account explicitly:

```bash
python manage.py create_superadmin --email you@domain --password 'A-strong-pass'
```

Keep that password out of source control, screenshots, documentation and chat logs. Use a secret manager or environment variable for hosted deployments.

Before exposing a deployment:

```bash
python manage.py security_check
python manage.py security_check --as-production
```

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

- **Trust is evidence, not decoration.** Each vibe receives a pipeline-written trust tier (`verified`, `scanned`, or unknown). Content changes reset the tier until the new bytes are checked.
- **Dependency safety is real.** Manifest dependencies are checked against the npm/PyPI registries to reduce AI-generated slopsquatting risk. Registry failures fail closed rather than pretending a package is malicious.
- **Program kinds are explicit.** Vibes can be games, APIs, mobile apps, notebooks, CLI tools and more. The platform says when a live preview is unavailable instead of faking one.
- **Discovery is personalised carefully.** The For You feed can learn from actions such as opening, starring, forking and trading, while explicit filters and non-default sorts stay truthful.
- **Git is real.** Smart HTTP supports clone/push flows subject to the same access, ownership and scanning rules as the web UI. A pushed project re-enters the scan queue.
- **Stars are a real ledger.** Trades move stars atomically between buyer and seller, and successful trades unlock paid ZIP downloads.
- **Paid downloads are protected.** ZIPs, forks, Git URLs and media paths cannot bypass the access checks.
- **Paystack checkout is gated.** Buy flows only appear when the required payment configuration is present and webhooks are verified.
- **Creator cash-outs exist.** Star balances can be held for payout and approved by an authorised operator; pending payment-provider transfers are not treated as paid automatically.
- **Battle votes do not inflate project stars.** Competitive voting and creator popularity remain separate signals.
- **Remix lineage is preserved.** Forks keep a `forked_from` relationship so visitors can see where an idea came from and how it travelled.
- **BlaqVibes Today is the return loop.** It combines the daily mission, creator momentum, feedback, notifications and remixable work into one short command center.
- **Nolo is honest about providers.** Claude/Gemini/Groq are used only when configured; otherwise the built-in helper is presented as such.
- **Nolo prompts are budgeted.** Prompt construction compresses stable instructions and dynamic input within explicit character/token budgets, with observable token estimates in the response metadata.
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

For architecture and detailed behaviour, see `docs/specs/`, `docs/demos/` and `docs/STABILITY.md`.
