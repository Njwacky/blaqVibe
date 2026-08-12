from functools import wraps
from django.shortcuts import redirect, render
from django.contrib import messages
import logging

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
                if not request.user.is_authenticated:
                    return render(request, '403.html', status=403)
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
