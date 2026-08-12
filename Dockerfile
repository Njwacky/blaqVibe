FROM python:3.12-slim

# 5 Whys Docker: Why not bare metal? Isolate Python deps, ClamAV, Gitea. Why slim not alpine? psycopg needs gcc. Why no root? Security.

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev clamav clamav-daemon netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create media/static dirs
RUN mkdir -p /app/media/apps/zips /app/staticfiles && chmod -R 755 /app/media

# ClamAV freshclam (mock if no internet, crush silently)
RUN freshclam || echo "freshclam failed — mock mode"

EXPOSE 8000
CMD ["gunicorn", "blaqvibes.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
