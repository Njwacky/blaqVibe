#!/usr/bin/env bash
# Local / CI entrypoint — same steps GitHub Actions should run.
# Copy docs/ci-github-actions.yml to .github/workflows/ci.yml if your
# GitHub token has the workflows permission.
set -euo pipefail
cd "$(dirname "$0")/.."

# --- gates first -----------------------------------------------------------
# The suite is the slowest step and the one most likely to be red for
# unrelated reasons. Under `set -e` a red suite used to strand everything
# below it, so the hardening gate had not been evaluated here in practice at
# all — a green-looking run meant nothing. These three gates need no database,
# so they run before anything can absorb them.

# What "a public host" actually means for this project, in one place. Note the
# explicit E2B_SANDBOX/DJANGO_PREVIEW=0: the live-preview detection in
# settings.py trusts those, and a sandbox runner would otherwise inject
# http://localhost:8000 into CSRF_TRUSTED_ORIGINS and be *audited as a preview
# box* instead of as blaqvibes.co.za.
PROD_POSTURE=(
  DEBUG=0 DJANGO_LOCAL_DEV=0 DJANGO_PREVIEW=0 E2B_SANDBOX=0
  SEED_DEMO=0 SEED_DEMO_FORCE=0 RATELIMIT_ENABLE=1
  # A deploy supplies these three; settings ship dev defaults for each (console
  # mailer, SQLite when DATABASE_URL is unset, an inferred localhost broker when
  # REDIS_URL is unset). python-dotenv never overrides an exported variable, so
  # these win over a contributor's local .env.
  # Nothing here connects anywhere: security_check audits settings values, so the
  # Redis/Postgres hosts do not have to exist.
  EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
  EMAIL_HOST=smtp.blaqvibes.co.za
  DATABASE_URL=postgres://ci:ci@db:5432/ci
  REDIS_URL=redis://redis:6379/0
  CELERY_EAGER=0
  # A throwaway key only — security_check never uses it for anything.
  SECRET_KEY='ci-only-production-posture-placeholder-please-never-reuse-07070A'
)

# Gate 1 — evaluate the SAME settings module as if it were pointed at a public
# host. This is what catches a future edit that quietly leaves
# SECURE_SSL_REDIRECT / HSTS / the dev SECRET_KEY reachable. --strict so a
# WARN blocks too: the gate's whole claim is zero findings.
# ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS are deliberately NOT pinned here: those
# defaults are exactly what a deploy inherits if it does not set them, so the
# gate has to look at the shipped values rather than be handed comfortable ones.
env "${PROD_POSTURE[@]}" python manage.py security_check --strict

# Gate 2 — the mirror image: the same production posture, but with the
# defaults a deployer actually inherits (no EMAIL_BACKEND, no DATABASE_URL).
# security_check must refuse that, and name both findings. Without this gate,
# gate 1 could be satisfied by the config it passes in — if the console-mailer
# or SQLite finding ever stopped firing, "ok — no findings" would still print.
defaults_out="$(mktemp)"
if env DEBUG=0 DJANGO_LOCAL_DEV=0 DJANGO_PREVIEW=0 E2B_SANDBOX=0 \
        SEED_DEMO=0 RATELIMIT_ENABLE=1 REDIS_URL= CELERY_EAGER=0 \
        SECRET_KEY='ci-only-production-posture-placeholder-please-never-reuse-07070A' \
        python manage.py security_check >"$defaults_out" 2>&1; then
  echo "FAIL: security_check accepted the shipped defaults on a public host — the console mailer, SQLite and missing-broker findings no longer fire." >&2
  cat "$defaults_out" >&2
  exit 1
fi
for finding in 'EMAIL_BACKEND is the console backend' 'DATABASES["default"] is SQLite' 'REDIS_URL is unset'; do
  grep -qF -- "$finding" "$defaults_out" || {
    echo "FAIL: security_check no longer reports '$finding' against the shipped defaults." >&2
    cat "$defaults_out" >&2
    exit 1
  }
done
rm -f "$defaults_out"
echo "ok — shipped defaults are refused on a public host (console mailer, SQLite, no broker)"

# Gate 3 — the seeder must stay refused in production posture. If this ever
# prints "Demo catalog ready", the known-password accounts are back on public
# hosts.
if env DEBUG=0 DJANGO_LOCAL_DEV=0 SEED_DEMO=0 \
       SECRET_KEY='ci-only-production-posture-placeholder-please-never-reuse-07070A' \
       python manage.py seed_demo >/dev/null 2>&1; then
  echo "FAIL: seed_demo ran without a dev posture — it mints README passwords on public hosts" >&2
  exit 1
fi

# --- the suite -------------------------------------------------------------
export DJANGO_LOCAL_DEV="${DJANGO_LOCAL_DEV:-1}"
export DJANGO_TEST="${DJANGO_TEST:-1}"
export SEED_DEMO="${SEED_DEMO:-0}"

python manage.py migrate
SEED_DEMO=1 python manage.py seed_demo
python manage.py test gallery users

python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'blaqvibes.settings')
os.environ.setdefault('DJANGO_LOCAL_DEV', '1')
import django
django.setup()
from gallery.models import AppProject
n = AppProject.objects.filter(status='published').count()
assert n >= 6, f'expected seeded catalog, got {n}'
print(f'ok — {n} published vibes')
PY
