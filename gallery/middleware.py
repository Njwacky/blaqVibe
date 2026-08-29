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

from http import cookies as http_cookies

from django.shortcuts import render

# Python 3.11's Morsel does not know Partitioned (CHIPS). Register it once
# so Set-Cookie can carry the flag browsers need for a third-party iframe.
http_cookies.Morsel._reserved.setdefault('partitioned', 'Partitioned')
http_cookies.Morsel._flags.add('partitioned')


class PreviewEmbedMiddleware:
    """Make CSRF/session cookies survive the Arena live-preview iframe.

    5 Whys: why middleware, not only settings?
    1. Why touch the response at all? SameSite=Lax cookies are first-party
       only. The preview is https://{port}-{id}.e2b.app inside a parent
       on another origin, so the browser never stores/sends csrftoken —
       Django then says "CSRF cookie not set."
    2. Why also key off the Host suffix? A runserver started without
       E2B_SANDBOX still serves that host. Settings-only would miss it.
    3. Why Partitioned? Chrome is dropping unpartitioned third-party
       cookies. CHIPS is what still works in an iframe.
    4. Why strip X-Frame-Options here? SAMEORIGIN / DENY blocks the
       Arena iframe; production keeps DENY because Host is not e2b.app.
    5. Why never rewrite cookies on blaqvibes.co.za? Production is
       first-party. Lax + Secure is the CSRF posture we want there.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        embed_host = host_needs_embed_cookies(request)
        # Only the e2b preview host. Production (blaqvibes.co.za) and
        # laptop localhost keep Lax cookies and X-Frame-Options — this
        # middleware must never weaken the real site.
        if not embed_host:
            return response
        from django.conf import settings
        response.headers.pop('X-Frame-Options', None)
        for name in (settings.CSRF_COOKIE_NAME, settings.SESSION_COOKIE_NAME):
            morsel = response.cookies.get(name)
            if morsel is None:
                continue
            morsel['samesite'] = 'None'
            morsel['secure'] = True
            morsel['partitioned'] = True
        return response


def host_needs_embed_cookies(request):
    """True only for the e2b preview hostname, never for blaqvibes.co.za."""
    try:
        host = (request.get_host() or '').split(':')[0].lower()
    except Exception:
        return False
    return host.endswith('.e2b.app') or host.endswith('.e2b.dev')


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
            # Ops probes stay live through a maintenance window: the LB and
            # alerting must see "up" while humans see the 503 page.
            or path in ('/healthz', '/readyz')
        ):
            return self._get_response(request)

        try:
            from users.models import SiteSettings
            site = SiteSettings.get()
            if site.maintenance:
                # Superadmins bypass the maintenance wall so they can
                # resolve the issue without needing a separate login flow.
                # getattr: this middleware sits AFTER AuthenticationMiddleware
                # in MIDDLEWARE (that placement is required — see settings.py),
                # but a request must never 500 the wall because a user object
                # is missing.
                user = getattr(request, 'user', None)
                if (
                    user is not None
                    and user.is_authenticated
                    and getattr(getattr(user, 'profile', None), 'role', '') == 'superadmin'
                ):
                    return self._get_response(request)
                return render(request, '503.html', status=503)
        except Exception:
            pass

        return self._get_response(request)