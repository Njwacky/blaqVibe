"""Friendly CSRF failure — the default Django page is a dead end.

5 Whys: why our own view?
1. Why not the stock 403? It tells people to "re-enable cookies" and
   stops. The actual bug on the live preview was SameSite=Lax inside
   an iframe — refresh after the cookie-flag fix is the recovery.
2. Why render a real template? The failure response still goes through
   process_response, so {% csrf_token %} here SETS the cookie the
   next POST needs.
3. Why keep the reason? Tests and support need the real Django phrase
   ("CSRF cookie not set.") without showing the scary default page.
4. Why never csrf_exempt login? That would fix the symptom by opening
   login CSRF. The cookie flags are the real fix.
5. Why a tiny module? settings.CSRF_FAILURE_VIEW is a dotted path;
   importing views.py from settings boot is how circular imports start.
"""
from django.shortcuts import render


def csrf_failure(request, reason=''):
    return render(
        request,
        '403_csrf.html',
        {'reason': reason, 'login_url': '/accounts/login/'},
        status=403,
    )
