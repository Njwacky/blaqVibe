import logging
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from users.decorators import moderator_required
from .models import AppProject, AppReport
from .reports import resolve_report

logger = logging.getLogger(__name__)


def sanitize_note(raw):
    """Staff notes are stored text: strip markup, cap the length."""
    try:
        from .prompt_sanitize import sanitize_prompt
        return sanitize_prompt(raw or '')[:400]
    except Exception:
        return (raw or '')[:400]


@moderator_required
def moderation_queue(request):
    pending = AppProject.objects.filter(status='pending').select_related('owner','category').order_by('-created_at')
    quarantined = AppProject.objects.filter(status='quarantined').select_related('owner','category').order_by('-created_at')
    open_reports = AppReport.objects.filter(status='open').count()
    # Account-level work (rule breaches, appeals) is a different queue from
    # scan verdicts — but it must be visible from here, or staff never open it.
    open_appeals = 0
    try:
        from users.models import QuarantineAppeal
        open_appeals = QuarantineAppeal.objects.filter(status='open').count()
    except Exception:
        logger.exception('open appeal count failed')
    return render(request, 'gallery/moderation_queue.html', {
        'pending': pending,
        'quarantined': quarantined,
        'open_reports': open_reports,
        'open_appeals': open_appeals,
    })

@moderator_required
def reports_queue(request):
    """Report triage — open reports first, resolved/ignored behind them.
    """
    now = timezone.now()
    open_reports = (
        AppReport.objects
        .filter(status='open')
        .select_related('project', 'project__owner', 'user', 'user__profile')
        .order_by('-created_at')
    )
    # One row per open report, but annotate how many open reports the
    # project has so a moderator can spot patterns without joining by hand.
    open_counts = dict(
        AppReport.objects
        .filter(status='open')
        .values('project_id')
        .annotate(n=Count('id'))
        .values_list('project_id', 'n')
    )
    open_list = list(open_reports)
    for report in open_list:
        report.project_open_count = open_counts.get(report.project_id, 1)
        # A vote "resolved" is not a dismissal of the other open reports;
        # if the vibe is already gone, close the whole project group.
        report.project_status = report.project.status

    handled = (
        AppReport.objects
        .exclude(status='open')
        .select_related('project', 'project__owner', 'user', 'handled_by')
        .order_by('-handled_at')[:50]
    )

    stats = {
        'open': len(open_list),
        'resolved': AppReport.objects.filter(status='resolved').count(),
        'ignored': AppReport.objects.filter(status='ignored').count(),
        'projects_flagged': len({r.project_id for r in open_list}),
    }
    return render(request, 'gallery/reports_queue.html', {
        'reports': open_list,
        'handled': handled,
        'stats': stats,
        'now': now,
        'decisions': (
            ('ignore', 'Dismiss — no action'),
            ('quarantine', 'Hold / quarantine vibe'),
            ('remove', 'Remove vibe (admin)'),
            ('delete', 'Delete vibe (admin)'),
        ),
    })

@moderator_required
def appeals_queue(request):
    """Account quarantine + appeals — staff-only triage for rule breaches.

    Three lists, in the order a moderator needs them:
      1. Open appeals — a person is waiting for a human answer. First.
      2. Live quarantines — who cannot post right now, and until when.
      3. Recent violations — the evidence, for anyone asking "why is this
         account held?" (expired holds are swept here, so the list is honest).
    """
    from users.models import QuarantineAppeal, RuleViolation, UserQuarantine
    from users.quarantine import sweep_expired

    # Somebody is looking at the list: close out holds whose clock has passed
    # and tell those people they can post again. No cron required.
    try:
        sweep_expired()
    except Exception:
        logger.exception('sweep_expired failed in appeals queue')

    now = timezone.now()
    open_appeals = list(
        QuarantineAppeal.objects
        .filter(status='open')
        .select_related('user', 'quarantine')
        .order_by('created_at')  # oldest first: nobody waits forever
    )
    for appeal in open_appeals:
        appeal.quarantine_strikes = appeal.quarantine.strike_count
        appeal.days_left = appeal.quarantine.days_left(now)

    active_quarantines = list(
        UserQuarantine.objects
        .filter(status='active', ends_at__gt=now)
        .select_related('user', 'imposed_by')
        .order_by('ends_at')
    )
    for quarantine in active_quarantines:
        quarantine.days_left_now = quarantine.days_left(now)

    handled_appeals = (
        QuarantineAppeal.objects
        .exclude(status='open')
        .select_related('user', 'reviewed_by', 'quarantine')
        .order_by('-reviewed_at')[:25]
    )
    recent_violations = (
        RuleViolation.objects
        .select_related('user')
        .order_by('-created_at')[:25]
    )
    stats = {
        'open_appeals': len(open_appeals),
        'active_accounts': len(active_quarantines),
        'violations_7d': RuleViolation.objects.filter(
            created_at__gte=now - timedelta(days=7)
        ).count(),
        'quarantined_total': UserQuarantine.objects.count(),
    }
    return render(request, 'gallery/appeals_queue.html', {
        'open_appeals': open_appeals,
        'active_quarantines': active_quarantines,
        'handled_appeals': handled_appeals,
        'recent_violations': recent_violations,
        'stats': stats,
        'decisions': QuarantineAppeal.DECISIONS,
        'violation_kinds': RuleViolation.KINDS,
    })


@moderator_required
@require_POST
def appeal_action(request, appeal_id):
    """Accept / deny / extend one appeal. POST only — it changes an account."""
    from users.models import QuarantineAppeal
    from users.quarantine import resolve_appeal

    appeal = get_object_or_404(
        QuarantineAppeal.objects.select_related('user', 'quarantine'),
        pk=appeal_id,
    )
    note = sanitize_note(request.POST.get('note', ''))
    result = resolve_appeal(
        appeal,
        actor=request.user,
        decision=request.POST.get('decision', ''),
        note=note,
    )
    if result['ok']:
        messages.success(request, result['message'])
    else:
        messages.error(request, result['message'])
    return redirect('appeals_queue')


@moderator_required
@require_POST
def quarantine_action(request, quarantine_id):
    """Lift or extend a live account quarantine, outside any appeal."""
    from users.models import UserQuarantine
    from users.quarantine import extend_quarantine, lift_quarantine

    quarantine = get_object_or_404(
        UserQuarantine.objects.select_related('user'),
        pk=quarantine_id,
    )
    action = request.POST.get('action', '')
    note = sanitize_note(request.POST.get('note', ''))
    if action == 'lift':
        lift_quarantine(quarantine, actor=request.user, note=note)
        messages.success(request, f'Quarantine lifted — @{quarantine.user.username} can post again.')
    elif action == 'extend':
        extend_quarantine(quarantine, actor=request.user, note=note)
        messages.success(request, f'30 days added — @{quarantine.user.username} is held until {quarantine.ends_label()}.')
    elif action == 'expire':
        quarantine.status = 'expired'
        quarantine.save(update_fields=['status'])
        messages.info(request, f'Marked expired for @{quarantine.user.username}.')
    else:
        messages.error(request, 'Unknown quarantine action.')
    return redirect('appeals_queue')


@moderator_required
@require_POST
def quarantine_new(request):
    """Staff-applied quarantine — the "any other rule breach" path.

    Offensive language is caught automatically. Everything else (harassment in
    a trade message, spam campaigns, impersonation) is a human judgement, so
    staff need one form to apply the same 30-day hold — with the same notice,
    the same inbox message and the same appeal box for the person.
    """
    from django.contrib.auth.models import User
    from users.quarantine import latest_quarantine, record_violation

    username = (request.POST.get('username', '') or '').strip().lstrip('@')
    kind = request.POST.get('kind', 'other')
    reason_detail = sanitize_note(request.POST.get('detail', ''))
    days = request.POST.get('days') or None
    try:
        days = int(days) if days else None
    except (TypeError, ValueError):
        days = None

    target = User.objects.filter(username__iexact=username).first()
    if target is None:
        messages.error(request, f'No account named @{username}.')
        return redirect('appeals_queue')
    if target.pk == request.user.pk:
        messages.error(request, 'You cannot quarantine your own account.')
        return redirect('appeals_queue')
    if getattr(target.profile, 'is_moderator', lambda: False)():
        messages.error(request, 'That account already has moderation powers — change their role first.')
        return redirect('appeals_queue')

    # ONE path: record the breach and let the policy apply the hold, with
    # `source='staff'` so the person's page says a human decided this.
    record_violation(
        target,
        kind=kind,
        surface='staff',
        detail=reason_detail or 'Staff decision.',
        evidence=reason_detail,
        days=days,
        source='staff',
        imposed_by=request.user,
    )
    quarantine = latest_quarantine(target)
    # The audit row is written inside users.quarantine (single place for every
    # path that can hold an account), so nothing extra is needed here.
    messages.success(
        request,
        f'@{target.username} quarantined until {quarantine.ends_label()} — they have been told, '
        f'and they can appeal.',
    )
    return redirect('appeals_queue')


@moderator_required
@require_POST
def report_action(request, report_id):
    """Handle one report. POST only; every action is a state change."""
    report = get_object_or_404(
        AppReport.objects.select_related('project', 'project__owner'),
        pk=report_id,
    )
    decision = request.POST.get('decision', '')
    note = request.POST.get('note', '')
    try:
        from .prompt_sanitize import sanitize_prompt
        note = sanitize_prompt(note or '')[:500]
    except Exception:
        note = (note or '')[:500]

    result = resolve_report(report, request.user, decision, note)
    if result.get('ok'):
        messages.success(request, result['message'])
    else:
        messages.error(request, result.get('message', 'Could not resolve this report.'))
    next_url = request.POST.get('next', '')
    if next_url.startswith('/reports/'):
        return redirect(next_url)
    return redirect('reports_queue')

@moderator_required
@require_POST
def moderation_action(request, slug):
    project = get_object_or_404(AppProject, slug=slug)
    action = request.POST.get('action')
    if action == 'approve':
        project.status = 'published'
        project.save(update_fields=['status'])
        # Human approval is a publish path too — grade from the recorded
        # evidence (snippet_scan for snippets, the scan chain for ZIPs) so
        # a moderator-approved vibe carries the same badge machinery.
        try:
            from .trust import apply_trust_grade
            apply_trust_grade(project)
        except Exception:
            pass
        from .models import ScanJob
        ScanJob.objects.update_or_create(project=project, defaults={'status': 'clean'})
    elif action == 'reject':
        project.status = 'quarantined'
        project.save(update_fields=['status'])
        from .models import ScanJob
        ScanJob.objects.update_or_create(project=project, defaults={'status': 'quarantined'})
    elif action == 'delete':
        if not request.user.profile.is_admin():
            return render(request, '403.html', status=403)
        # Same rule as owner deletes: paid vibes soft-delete so buyers keep
        # their receipts and downloads; unpaid vibes hard-delete.
        from .lifecycle import remove_project
        remove_project(project)
        return redirect('moderation_queue')
    return redirect('moderation_queue')
