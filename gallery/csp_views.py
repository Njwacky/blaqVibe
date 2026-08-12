import json, logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
logger = logging.getLogger(__name__)

# 5 Whys CSP Report-Only: Why not enforce immediately? Enforce breaks Tailwind CDN if we mis-allow. Report-Only logs violations to Sentry first, then enforce after 1 week.

@csrf_exempt
def csp_report(request):
    """Backend only, crush silently — receives browser CSP violation reports, logs to Sentry."""
    try:
        if request.method == 'POST':
            body = request.body.decode('utf-8', errors='ignore') if request.body else ""
            try:
                data = json.loads(body) if body else {}
            except:
                data = {"raw": body[:500]}
            # Log + Sentry, no secrets in JS, never crash
            logger.warning(f"CSP violation: {data}")
            try:
                import sentry_sdk
                sentry_sdk.capture_message(f"CSP violation: {str(data)[:300]}")
            except: pass
        return HttpResponse("", status=204)
    except Exception as e:
        logger.exception(f"csp_report crush: {e}")
        return HttpResponse("", status=204)  # crush silently, return 204
