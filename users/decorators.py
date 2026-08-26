from functools import wraps
import logging

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render

logger = logging.getLogger(__name__)

def _has_role(user, required):
    try:
        role = getattr(user.profile, 'role', 'user')
        order = {'user':0,'moderator':1,'admin':2,'superadmin':3}
        return order.get(role,0) >= order.get(required,0)
    except Exception:
        return False

def role_required(required_role):
    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            try:
                # 5 Whys: why redirect instead of 403 for anonymous?
                # A visitor hitting /admin/dashboard/ (or the Django-shaped
                # /admin/) is trying to sign in, not break in. A 403 fork
                # page with no login form is why "admin password never
                # works" — they never reached the form. Authenticated
                # users without the role still 403 (not a coin-flip login).
                if not request.user.is_authenticated:
                    return redirect_to_login(
                        request.get_full_path(), settings.LOGIN_URL,
                    )
                if not _has_role(request.user, required_role):
                    # Crush silently + Sentry, safe page
                    try:
                        import sentry_sdk
                        sentry_sdk.capture_message(f"403 role {required_role}: {request.user} at {request.path}")
                    except Exception: pass
                    logger.warning(f"403 role {required_role} for {request.user}")
                    return render(request, '403.html', status=403)
                return view(request, *args, **kwargs)
            except Exception as e:
                logger.exception(f"role check crush: {e}")
                return render(request, '403.html', status=403)
        return _wrapped
    return decorator

moderator_required = role_required('moderator')
admin_required = role_required('admin')
superadmin_required = role_required('superadmin')
