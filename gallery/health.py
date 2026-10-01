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
   browsing still work while a scanner or optional broker is unavailable.
   Queue reachability is not a claim that a runner is currently alive.
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
    """Report the database queue, or the explicitly selected Celery broker.

    This is queue reachability, not runner liveness. Local eager mode is
    healthy by definition; an absent optional broker is reported as disabled.
    """
    from .scan_queue import queue_backend
    if queue_backend() == 'database':
        try:
            from .models import ScanJob
            ScanJob.objects.exists()  # An empty queue is healthy too.
            return True, 'database_queue'  # Not a claim that a runner is alive.
        except Exception:
            logger.exception('readiness database queue check failed')
            return False, 'unavailable'
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        return True, 'eager'
    url = getattr(settings, 'CELERY_BROKER_URL', '') or ''
    if not url:
        return True, 'disabled'
    try:
        _ping_redis(url)
        return True, 'ok'
    except Exception as exc:
        logger.exception('readiness queue check failed')
        return False, 'unavailable'

def _ping_redis(url):
    # Optional dependency. The default deployment never imports redis-py.
    import redis
    redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2).ping()


def readiness(request):
    """Ready to serve? DB must answer SELECT 1; queue is reported, not gated."""
    db_ok, db_detail = _db_ok()
    queue_ok, queue_detail = _queue_state()

    checks = {
        'database': {'ok': db_ok, 'detail': db_detail},
        'queue': {'ok': queue_ok, 'detail': queue_detail,
                  'backend': getattr(settings, 'SCAN_QUEUE_BACKEND', 'database')},
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
