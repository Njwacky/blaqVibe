from functools import wraps
import logging

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import Http404
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
                # Redirect (rather than 403) for anonymous users: a visitor
                # hitting /admin/dashboard/ (or the Django-shaped /admin/) is
                # trying to sign in, not break in. A 403 page with no login
                # form is why "admin password never works" — they never reached
                # the form. Authenticated users without the role still 403.

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
            except Http404:
                # A missing account is a 404, not a role problem. The blanket
                # handler below used to turn every Http404 raised inside a
                # protected view into "It's not you, it's me" — so an operator
                # who searched a deleted username was told they lacked access.
                raise
            except Exception as e:
                logger.exception(f"role check crush: {e}")
                return render(request, '403.html', status=403)
        return _wrapped
    return decorator

moderator_required = role_required('moderator')
admin_required = role_required('admin')
superadmin_required = role_required('superadmin')

def not_quarantined(view):
    """A quarantined account may READ; it may not post NEW public content.

    Put this on the write views (publish, comment, review, PR, profile text,
    skill). GET/HEAD/OPTIONS always pass, so the person can still browse,
    read their notice, and use the appeal form — blocking those is how a 30-day
    hold turns into "the site is broken" support mail.

    Ordering: apply it as the OUTERMOST decorator. It is a cheap DB read and
    it answers before rate-limit bookkeeping or form parsing; anonymous
    visitors pass through untouched (active_quarantine returns None).
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            try:
                from .quarantine import active_quarantine, quarantine_block_message
                quarantine = active_quarantine(getattr(request, 'user', None))
            except Exception:
                logger.exception('quarantine check crush at %s', request.path)
                quarantine = None
            if quarantine is not None:
                from django.contrib import messages
                from django.shortcuts import redirect
                try:
                    messages.error(request, quarantine_block_message(quarantine))
                except Exception:
                    pass
                return redirect('quarantine_notice')
        return view(request, *args, **kwargs)
    return _wrapped
