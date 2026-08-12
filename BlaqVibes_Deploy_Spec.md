# Live Preview Deploy — 1-Click Run (Spec, 5 Whys)

**Feature:** Click ▶ Run on any full app → spins up Docker container → live URL `stock-app-vibes-123.blaqvibes.run` for 1 hour. Why? Unpublished apps finally run without `pip install`.

**5 Whys:**
1. Why Docker not just `python manage.py runserver`? Each app has different stack (Django, React, HTML). Docker isolates.
2. Why 1 hour? Free tier, auto-cleanup, no zombie containers.
3. Why `stock-app-vibes-123.blaqvibes.run` not `blaqvibes.co.za/deploy/123`? Subdomain looks real, like Vercel. For MVP, path is fine, subdomain via wildcard DNS later.
4. Why backend not JS? Docker start is `subprocess.run(['docker','run',...])`, never in JS.
5. Why crush silently? Docker not installed in dev → mock deploy (serve ZIP via Python http.server) still gives live URL.

**How (MVP, no real Docker needed):**
- Model `Deploy(project, owner, token, status, live_url, expires_at)` — token is `stock-app-vibes-123`
- `POST /app/<slug>/run/` → create `Deploy` with `expires_at = now + 1h`, status `running`, `live_url = f"/deploy/{token}/"` (MVP) or `f"https://{token}.blaqvibes.run"` (prod with wildcard)
- For MVP, live URL just serves the app's `index.html` from ZIP via `deploy_view` that extracts and serves static files. No Docker needed, but structure ready for `docker run -d -p 8001:8000 {image}`.
- Auto-cleanup via Celery beat every 5 min: `Deploy.objects.filter(expires_at__lt=now).update(status='expired')`.

**Why no shortcuts:** Every deploy is queued, even concurrent Run clicks serialize via `scan` queue, 1-hour TTL, no secrets in JS.
