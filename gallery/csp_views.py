import json, logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit
logger = logging.getLogger(__name__)


@ratelimit(key='ip', rate='60/m', method='POST')
@csrf_exempt
def csp_report(request):
    """Backend only, crush silently — receives browser CSP violation reports, logs to Sentry."""
    try:
        if request.method == 'POST':
            body = request.body.decode('utf-8', errors='ignore') if request.body else ""
            try:
                data = json.loads(body) if body else {}
            except Exception:
                data = {"raw": body[:500]}
            logger.warning(f"CSP violation: {data}")
            try:
                import sentry_sdk
                sentry_sdk.capture_message(f"CSP violation: {str(data)[:300]}")
            except Exception: pass
        return HttpResponse("", status=204)
    except Exception as e:
        logger.exception(f"csp_report crush: {e}")
        return HttpResponse("", status=204)
