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
