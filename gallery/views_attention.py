"""Attention centre — the owner's side of a duplicate / malfunction case.

Every view here is owner-scoped BY QUERYSET (`user=request.user`), so a
stranger's case id is a 404 rather than a 403: a 403 would confirm the case
exists to somebody guessing ids. Every write is POST-only, rate-limited, and
delegates the actual state change to gallery.attention — this file decides WHO
may ask, never WHAT happens.

The one exception to "views are thin" is `attention_status`: it is the JSON the
browser polls every 30 minutes so an open tab re-nags without a reload, and it
deliberately carries no project data (counts and clocks only).
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from django_ratelimit.decorators import ratelimit

from . import attention
from .models import AttentionCase
from .notify import inbox_queryset

logger = logging.getLogger(__name__)

# A decision endpoint is a destructive-ish action behind a session cookie, so it
# gets the same ceiling the rest of the write endpoints use: enough for a person
# cleaning up six cases in a row, far too little for a scripted loop.
DECISION_RATE = '30/h'

# How many waiting cases get the full per-candidate scoring pass on the centre
# page. Bounded because scoring is a handful of queries per candidate.
RANK_LIMIT = 12

def _case_for(request, case_id) -> AttentionCase:
    """Owner-scoped fetch. 404 for anybody else, including staff: this is the
    owner's workshop decision, not a moderation queue."""
    return get_object_or_404(AttentionCase, pk=case_id, user=request.user)

def _finish(request, result, fallback='attention_center'):
    """One way to turn an engine result into a message + redirect."""
    if result.get('ok'):
        messages.success(request, result.get('message', 'Done.'))
    else:
        messages.error(request, result.get('message', 'That did not work. Try again.'))
    case = result.get('case')
    if case is not None and result.get('ok'):
        return redirect('attention_case', case_id=case.pk)
    return redirect(fallback)

@login_required
def attention_center(request):
    """Everything the platform is waiting on, most severe first.

    Opens with a live detection pass for THIS owner: the page must never say
    "nothing needs you" while a duplicate uploaded two minutes ago sits
    undetected waiting for the hourly sweep.
    """
    try:
        attention.detect_for_user(request.user)
    except Exception:
        logger.exception('attention detect_for_user failed on page load')

    cases = (
        AttentionCase.objects
        .filter(user=request.user)
        .prefetch_related('candidates', 'candidates__project')
        .select_related('subject', 'keeper')[:100]
    )
    now = timezone.now()
    waiting = []
    answered = []
    for case in cases:
        # Seeing a decision starts its 24h clock. This is the moment the
        # promise "if you open it and do nothing for 24 hours" begins, so it is
        # recorded on the row and shown on the page.
        if case.status == 'decided' and case.dropped:
            try:
                attention.acknowledge(case)
            except Exception:
                logger.exception('acknowledge failed case=%s', case.pk)
        case.countdown = attention.countdown(case, now=now)
        (waiting if case.status in ('open', 'decided') else answered).append(case)
    waiting.sort(key=lambda c: (attention.category_meta(c.severity)['rank'], c.created_at))

    # Rank only the cases still waiting, and only the first few: scoring reads
    # trades, sales and remix children per candidate, so an owner with a long
    # history of answered cases must not pay for ranking decisions they already
    # made. The ranked evidence is what the "BlaqVibes would keep this" badge
    # and the per-copy reasons are drawn from.
    for case in waiting[:RANK_LIMIT]:
        try:
            case.ranked = attention.rank_candidates(case)
        except Exception:
            logger.exception('rank_candidates failed case=%s', case.pk)
            case.ranked = []

    show = request.GET.get('show', 'waiting')
    return render(request, 'gallery/attention.html', {
        'waiting': waiting,
        'answered': answered,
        'show': show if show in ('waiting', 'answered') else 'waiting',
        'legend': attention.severity_legend(),
        'decision_days': attention.decision_days(),
        'reminder_minutes': attention.reminder_minutes(),
        'final_delete_hours': attention.final_delete_hours(),
        'summary': attention.summary(request.user),
    })

@login_required
def attention_case_detail(request, case_id):
    """One case, with the ranked evidence and the buttons that act on it."""
    case = _case_for(request, case_id)
    if case.status == 'decided' and case.dropped:
        try:
            attention.acknowledge(case)
            case.refresh_from_db()
        except Exception:
            logger.exception('acknowledge failed case=%s', case.pk)
    ranked = []
    try:
        ranked = attention.rank_candidates(case) if case.status in ('open', 'decided') else []
    except Exception:
        logger.exception('rank_candidates failed case=%s', case.pk)
    return render(request, 'gallery/attention_case.html', {
        'case': case,
        'ranked': ranked,
        'candidates': list(case.candidates.select_related('project')),
        'countdown': attention.countdown(case),
        'strategy': attention.KEEPER_STRATEGY,
        'final_delete_hours': attention.final_delete_hours(),
        'decision_days': attention.decision_days(),
    })

@login_required
@require_POST
@ratelimit(key='user', rate=DECISION_RATE, method='POST', block=True)
def attention_decide(request, case_id):
    """KEEP THIS ONE — the owner's pick. `project_id` must be one of the case's
    own candidates; anything else is refused (never a 404 on a valid case, so
    the owner learns the real problem)."""
    case = _case_for(request, case_id)
    try:
        project_id = int(request.POST.get('project_id') or 0)
    except (TypeError, ValueError):
        project_id = 0
    keeper = None
    if project_id:
        keeper = get_object_or_404(case.candidates.filter(project__isnull=False), project_id=project_id).project
    result = attention.decide(case, keeper_project=keeper, actor=request.user, source='user')
    return _finish(request, result)

@login_required
@require_POST
@ratelimit(key='user', rate=DECISION_RATE, method='POST', block=True)
def attention_delegate(request, case_id):
    """LET BLAQVIBES DECIDE — same code path as the 7-day expiry, so asking and
    waiting produce the identical answer with the identical explanation."""
    case = _case_for(request, case_id)
    result = attention.delegate(case, actor=request.user)
    return _finish(request, result)

@login_required
@require_POST
@ratelimit(key='user', rate=DECISION_RATE, method='POST', block=True)
def attention_reopen(request, case_id):
    """LET ME CHOOSE — take the decision back. Restores every parked copy
    first: choosing between a live build and an erased one is not a choice."""
    case = _case_for(request, case_id)
    result = attention.reopen(case, actor=request.user)
    return _finish(request, result)

@login_required
@require_POST
@ratelimit(key='user', rate=DECISION_RATE, method='POST', block=True)
def attention_dismiss(request, case_id):
    """KEEP BOTH / I'LL FIX IT — the owner overrules the detector, once."""
    case = _case_for(request, case_id)
    reason = request.POST.get('reason', '')
    result = attention.dismiss(case, actor=request.user, reason=reason)
    return _finish(request, result)

@login_required
@require_POST
@ratelimit(key='user', rate=DECISION_RATE, method='POST', block=True)
def attention_delete_now(request, case_id, candidate_id):
    """FINAL DELETE — the owner's own hand on the only hard-delete path."""
    case = _case_for(request, case_id)
    candidate = get_object_or_404(case.candidates, pk=candidate_id)
    result = attention.final_delete(case, candidate, actor=request.user)
    if result.get('ok'):
        messages.success(request, result.get('message', 'Deleted.'))
    else:
        messages.error(request, result.get('message', 'That did not work.'))
    return redirect('attention_case', case_id=case.pk)

@login_required
@require_POST
@ratelimit(key='user', rate=DECISION_RATE, method='POST', block=True)
def attention_undo(request, case_id, candidate_id):
    """UNDO — put a parked copy back before the erase happens."""
    case = _case_for(request, case_id)
    candidate = get_object_or_404(case.candidates, pk=candidate_id)
    if candidate.outcome != 'parked' or candidate.project is None:
        messages.error(request, 'That copy is already gone — there is nothing to undo.')
        return redirect('attention_case', case_id=case.pk)
    from .lifecycle import restore_project
    before = (candidate.snapshot or {}).get('status_before_park') or 'pending'
    try:
        restore_project(candidate.project, before)
    except Exception:
        logger.exception('restore failed case=%s candidate=%s', case.pk, candidate.pk)
        messages.error(request, 'Could not restore that copy. Try again.')
        return redirect('attention_case', case_id=case.pk)
    now = timezone.now()
    candidate.outcome = 'restored'
    candidate.role = ''
    candidate.restored_at = now
    candidate.save(update_fields=['outcome', 'role', 'restored_at'])
    remaining = case.candidates.filter(outcome='parked', project__isnull=False).count()
    if remaining == 0:
        case.status = 'restored'
        case.final_delete_at = None
        case.dismissed_reason = 'not_a_duplicate' if case.kind == 'duplicate' else 'will_fix'
        case.save(update_fields=['status', 'final_delete_at', 'dismissed_reason', 'updated_at'])
    attention.notify_case(case)
    messages.success(request, f'“{candidate.title}” is back. Nothing will be erased.')
    return redirect('attention_case', case_id=case.pk)

@login_required
def attention_status(request):
    """JSON for the 30-minute re-nag in an open tab (static/gallery/js/attention.js).

    Counts and clocks only — no titles, no slugs, no ids. A poll endpoint that
    leaks project data would be a way to enumerate somebody's workshop.
    """
    summary = attention.summary(request.user)
    unread = 0
    try:
        unread = request.user.notifications.filter(is_read=False).count()
    except Exception:
        unread = 0
    next_reminder = None
    if summary['open'] or summary['awaiting_delete']:
        next_reminder = attention.reminder_minutes() * 60
    return JsonResponse({
        'ok': True,
        'open': summary['open'],
        'critical': summary['critical'],
        'awaiting_delete': summary['awaiting_delete'],
        'oldest_days': summary['oldest_days'],
        'due_reminder': summary['due_reminder'],
        'reminder_seconds': next_reminder,
        'unread': unread,
        'url': '/attention/',
    }, headers={'Cache-Control': 'no-store'})
