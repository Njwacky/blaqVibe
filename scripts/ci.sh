#!/usr/bin/env bash
# Local / CI entrypoint — same steps GitHub Actions should run.
# Copy docs/ci-github-actions.yml to .github/workflows/ci.yml if your
# GitHub token has the workflows permission.
set -euo pipefail
cd "$(dirname "$0")/.."
export DJANGO_LOCAL_DEV="${DJANGO_LOCAL_DEV:-1}"
export DJANGO_TEST="${DJANGO_TEST:-1}"
export SEED_DEMO="${SEED_DEMO:-0}"

python manage.py migrate
SEED_DEMO=1 python manage.py seed_demo
python manage.py test gallery users

# Hardening gate: evaluate the SAME settings module as if it were pointed at a
# public host (DEBUG off, no dev posture). This is what catches a future edit
# that quietly leaves SECURE_SSL_REDIRECT / HSTS / the dev SECRET_KEY reachable.
# A throwaway key only — security_check never uses it for anything.
DEBUG=0 DJANGO_LOCAL_DEV=0 SEED_DEMO=0 RATELIMIT_ENABLE=1 \
  SECRET_KEY='ci-only-production-posture-placeholder-please-never-reuse-07070A' \
  python manage.py security_check

# The seeder must stay refused in production posture. If this ever prints
# "Demo catalog ready", the known-password accounts are back on public hosts.
if DEBUG=0 DJANGO_LOCAL_DEV=0 SEED_DEMO=0 SECRET_KEY='ci-only-production-posture-placeholder-please-never-reuse-07070A' \
     python manage.py seed_demo >/dev/null 2>&1; then
  echo "FAIL: seed_demo ran without a dev posture — it mints README passwords on public hosts" >&2
  exit 1
fi

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
