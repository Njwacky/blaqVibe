"""The quarantined person's side of the decision — notice, countdown, appeal.

Why a page and not just a toast? A toast is gone in ten seconds. A 30-day
hold needs a place that still answers the question at day 12: what happened,
what exactly is paused, when does it lift, and where is the box to say "that
was a misunderstanding". That page is /quarantine/.

It is deliberately NOT decorated with `not_quarantined` — filing an appeal is
the one write a quarantined account is still allowed to make.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from .models import RuleViolation
from .quarantine import (
    active_quarantine,
    can_appeal,
    latest_quarantine,
    open_appeal,
    submit_appeal,
    sweep_expired,
)

logger = logging.getLogger(__name__)


@login_required
@ratelimit(key='user', rate='10/h', method='POST')
@require_http_methods(['GET', 'POST'])
def quarantine_notice(request):
    """The person's own quarantine notice + appeal form."""
    # Somebody is looking at the truth right now, so this is the cheap moment
    # to close out holds whose clock has run out (and tell them it is over).
    try:
        sweep_expired()
    except Exception:
        logger.exception('sweep_expired failed')

    live = active_quarantine(request.user)
    quarantine = live or latest_quarantine(request.user)

    if request.method == 'POST' and quarantine is not None:
        if getattr(request, 'limited', False):
            messages.error(request, 'Too many appeal attempts — try again in an hour.')
            return redirect('quarantine_notice')
        from gallery.prompt_sanitize import sanitize_prompt
        message_text = sanitize_prompt(request.POST.get('message', ''))[:2000]
        appeal, error = submit_appeal(quarantine, message_text)
        if appeal is not None:
            messages.success(
                request,
                'Appeal sent — a moderator reads every one. The answer lands in your inbox '
                '(and your email) either way.',
            )
        else:
            messages.error(request, error)
        return redirect('quarantine_notice')

    appeal_allowed, appeal_blocked_reason = (False, '')
    if live is not None:
        appeal_allowed, appeal_blocked_reason = can_appeal(live)

    violations = []
    if quarantine is not None:
        violations = (
            RuleViolation.objects
            .filter(user=request.user)
            .order_by('-created_at')[:10]
        )

    return render(request, 'users/quarantine.html', {
        'quarantine': quarantine,
        'live': live,
        'violations': violations,
        'open_appeal': open_appeal(live) if live else open_appeal(quarantine),
        'last_appeal': (quarantine.appeals.order_by('-created_at').first() if quarantine else None),
        'appeal_allowed': appeal_allowed,
        'appeal_blocked_reason': appeal_blocked_reason,
    })
