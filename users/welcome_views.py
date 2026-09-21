"""The first-time welcome overlay endpoint.

The overlay itself is HTML/CSS/JS inlined in the site base template — a modal
that travels with every page until `Profile.overlay_seen` is true. This module
is the state machine's one write:

    /welcome/seen   -> POST                ("I saw it", once per account)

The read half lives in the base template: the include and its boot script are
only emitted while `user.profile.overlay_seen` is false, so a separate
state-GET endpoint would carry the same answer in two places with no caller.
"""

import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from . import welcome

logger = logging.getLogger(__name__)


# Tab-swarm protection: the overlay is rendered once per open tab that has not
# answered yet, so a user with ten tabs closing at once could POST ten times.
# mark_seen is an idempotent UPDATE (harmless), but the write is still bounded
# per IP like every other write endpoint.
@require_POST
@login_required
@ratelimit(key='ip', rate='30/m', method='POST', block=True)
def welcome_seen(request):
    """Persist "I saw the welcome overlay" so it never returns."""
    welcome.mark_seen(request.user)
    return JsonResponse({'ok': True})
