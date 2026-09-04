"""Liveness and readiness endpoints — `/healthz` and `/readyz`.

Why separate endpoints?
1. Liveness (`/healthz`) answers "is the process alive?" — it must stay
   200 even if the database or broker is down, otherwise an orchestrator
   would restart every worker in a chain reaction during a DB outage.
   It deliberately touches NO external systems and NO settings beyond
   constants, so it cannot be the thing that is broken.
2. Readiness (`/readyz`) answers "can this process serve requests?" — it
   checks the database (a web request that cannot read the DB is broken).
   It only reports the queue/broker state instead of failing: reads and
   browsing still work while Redis is down, and the celery service has its
   own healthcheck, so the web container should not be killed for it.
3. Both are unauthenticated, GET-only, JSON, no-store, and bypass the
   maintenance wall (a 503 on the health path would hide "we are up but
   under maintenance" from load balancers and alerting).
"""
import logging

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone

from django.conf import settings

logger = logging.getLogger(__name__)

PROBE_VERSION = '1'

def _now():
    return timezone.now().isoformat()

def liveness(request):
    """Process alive? Always 200. No DB, no cache, no broker — ever."""
    return JsonResponse({
        'status': 'ok',
        'service': 'blaqvibes',
        'probe': 'liveness',
        'version': PROBE_VERSION,
        'time': _now(),
    }, headers={'Cache-Control': 'no-store'})

def _db_ok():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return True, 'ok'
    except Exception as exc:  # never let a probe 500 the probe
        # The real error is logged server-side; the public payload must not
        # carry host/port/db/user strings (see readiness below).
        logger.exception('readiness database check failed')
        return False, 'unavailable'

def _queue_state():
    """Report Celery/broker state. Eager/CELERY_EAGER is a local dev mode,
    so it is healthy by definition. Without a broker URL the app runs
    without async work in some deployments — report 'disabled', not an error."""
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        return True, 'eager'
    url = getattr(settings, 'CELERY_BROKER_URL', '') or ''
    if not url:
        return True, 'disabled'
    try:
        import redis as redis_lib
        client = redis_lib.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=2,
        )
        client.ping()
        return True, 'ok'
    except Exception as exc:
        logger.exception('readiness queue check failed')
        return False, 'unavailable'

def readiness(request):
    """Ready to serve? DB must answer SELECT 1; queue is reported, not gated."""
    db_ok, db_detail = _db_ok()
    queue_ok, queue_detail = _queue_state()

    checks = {
        'database': {'ok': db_ok, 'detail': db_detail},
        'queue': {'ok': queue_ok, 'detail': queue_detail},
    }
    payload = {
        'status': 'ok' if db_ok else 'unavailable',
        'service': 'blaqvibes',
        'probe': 'readiness',
        'version': PROBE_VERSION,
        'time': _now(),
        'checks': checks,
    }
    return JsonResponse(
        payload,
        status=200 if db_ok else 503,
        headers={'Cache-Control': 'no-store'},
    )
