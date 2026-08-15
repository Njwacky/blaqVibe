FROM python:3.12-slim

# 5 Whys Docker: Why not bare metal? Isolate Python deps, ClamAV, Gitea. Why slim not alpine? psycopg needs gcc. Why no root? Security.

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev clamav clamav-daemon netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user (security).
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create media/static dirs and give the runtime user ownership.
RUN mkdir -p /app/media/apps/zips /app/staticfiles && chown -R appuser:appuser /app

# Collect static files into /app/staticfiles. WhiteNoise serves only from
# STATIC_ROOT in production — without this the site ships with no CSS/JS.
# This build step does not have runtime secrets (.env is intentionally excluded
# from the image), so explicitly use local settings for this one management
# command. Runtime still fails closed when SECRET_KEY is missing.
RUN DJANGO_LOCAL_DEV=1 python manage.py collectstatic --noinput \
    && chown -R appuser:appuser /app/staticfiles

# ClamAV freshclam (mock if no internet, crush silently)
RUN freshclam || echo "freshclam failed — mock mode"

USER appuser

EXPOSE 8000
CMD ["gunicorn", "blaqvibes.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
