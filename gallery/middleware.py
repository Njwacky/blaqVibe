"""
Maintenance mode middleware — shows a 503 page when SiteSettings.maintenance
is True for all users except superadmins.

5 Whys:
1. Why middleware not a view decorator? Maintenance must block EVERY route
   (auth, admin, API, static) before any view code runs — a decorator on
   every view would miss new views and would fire after middleware that
   touches the DB (sessions, auth).
2. Why not a context-processor flag that templates check? Templates are
   the last layer; a CSRF token or a cache miss would still generate a
   full page with 200 status, telling crawlers the site is up.
3. Why skip static/media/admin paths? The maintenance page itself needs
   CSS/images, and a superadmin fixing the cause needs the admin panel.
4. Why default False? The site ships live; maintenance is an exceptional
   state set by ops during deploys or DB migrations.
5. Why catch every exception silently? A broken SiteSettings row must not
   take the whole site down — the middleware falls through to normal
   operation so the admin can fix it.
"""

from django.shortcuts import render


class MaintenanceModeMiddleware:
    """Responds 503 for all public paths when maintenance mode is on.

    Superadmins (role='superadmin') are exempt so they can investigate
    and fix issues without logging in through a broken auth page first.
    The login/signup/password-reset paths are also always accessible so
    that a superadmin who was logged out can still get back in.
    """

    def __init__(self, get_response):
        self._get_response = get_response

    def __call__(self, request):
        path = request.path_info

        # Always allow static/media assets (the 503 page itself needs CSS),
        # the real admin panel, the honeypot admin, and login/signup/password
        # reset so superadmins can authenticate during a maintenance window.
        if (
            path.startswith('/static/')
            or path.startswith('/media/')
            or path.startswith('/blaq-admin-secure/')
            or path.startswith('/admin/')
            or path.startswith('/accounts/login')
            or path.startswith('/accounts/signup')
            or path.startswith('/accounts/password')
            or path.startswith('/accounts/verify')
            or path.startswith('/accounts/reset')
        ):
            return self._get_response(request)

        try:
            from users.models import SiteSettings
            site = SiteSettings.get()
            if site.maintenance:
                # Superadmins bypass the maintenance wall so they can
                # resolve the issue without needing a separate login flow.
                if (
                    request.user.is_authenticated
                    and getattr(request.user.profile, 'role', '') == 'superadmin'
                ):
                    return self._get_response(request)
                return render(request, '503.html', status=503)
        except Exception:
            pass

        return self._get_response(request)