"""Report triage — create, escalate, resolve, and audit user reports.

5 Whys — why is this its own module instead of a fat view?
1. Why not put it in gallery/views.py? A report is created from one view
   and resolved from another; a shared module means the two can never
   disagree about what a "report" is.
2. Why not keep the rules in the model? The model already enforces the
   shape (status choices, outcome choices); the *policy* — who may act,
   what each decision does to a project, who gets told — is business
   logic, not schema.
3. Why is every state transition declared here instead of inline? If a
   moderator action ever runs from two routes (web + API + a future
   management command), one place must be the single source of truth.
4. Why return a structured result instead of raising exceptions for the
   normal cases? An already-resolved report is a normal race, not a bug;
   the view needs a message to show, and an exception would make every
   page a 500.
5. Why audit inside the same transaction as the action? An AdminLog row
   written after the state change can be lost in a crash, leaving an
   action nobody can trace.

The five Whys for the decision matrix:
1. Why decisions are 'ignore' / 'quarantine' / 'remove' / 'delete' and
   nothing else? The only real question a moderator has is "did this
   violate?"; every other wording is cosmetic.
2. Why 'remove' and 'delete' are separate yet both go through
   lifecycle.remove_project? Because the *content* change is identical —
   soft-delete when money moved, hard-delete when nothing was ever paid —
   and lifecycle is the single implementation of that rule.
3. Why does ignore resolve the report with outcome 'no_action'? A report
   that has been deliberately dismissed must be distinguishable from one
   that was never looked at.
4. Why does one handled report auto-resolve its siblings on the same
   project? Leaving three open rows side-by-side after the vibe is gone
   is a queue that can never be emptied.
5. Why notify moderators on *creation* and the owner on *resolution*?
   A report nobody is told about is a queue that only the admin page can
   discover; an owner told nothing is a user who logs in to a vanished
   vibe with no explanation.
"""
from __future__ import annotations

import logging

from datetime import timedelta

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .lifecycle import remove_project
from .models import AppProject, AppReport
from .notify import notify

logger = logging.getLogger(__name__)

VALID_DECISIONS = ('ignore', 'quarantine', 'remove', 'delete')
OUTCOME_BY_DECISION = {
    'ignore': 'no_action',
    'quarantine': 'quarantined',
    'remove': 'removed',
    'delete': 'deleted',
}


def _is_moderator(user) -> bool:
    try:
        return bool(user.profile.is_moderator())
    except Exception:
        return False


def _is_admin(user) -> bool:
    try:
        return bool(user.profile.is_admin())
    except Exception:
        return False


def moderators_queryset():
    """Active staff in every moderator-bearing role, in stable order."""
    return (
        User.objects
        .filter(is_active=True)
        .filter(Q(profile__role='moderator') | Q(profile__role='admin') | Q(profile__role='superadmin'))
        .order_by('username')
        .select_related('profile')
    )


def moderators_to_notify(reporter=None):
    """Staff who should open their inbox when a report arrives.

    `reporter` is normally anonymous/regular; if a moderator ever files a
    report we still tell the *other* staff — the reporter already knows
    the report exists, and notifying them is noise.
    """
    qs = moderators_queryset()
    if reporter is not None and reporter.pk is not None:
        qs = qs.exclude(pk=reporter.pk)
    return list(qs[:50])


def create_report(project: AppProject, user, reason: str, details: str, cooldown_hours: int = 24) -> tuple[AppReport, bool]:
    """Create one report and notify staff — the only creation path.

    Returns (report, created). `created=False` means the same user already
    has an open report about this project inside the cooldown window; we
    return that row instead of minting a duplicate.

    5 Whys:
    1. Why notify inside creation and not in the view? The view has no
       knowledge of who staff are or which notification channel exists;
       it should stay a thin HTTP boundary (parse input, redirect).
    2. Why notify every staff member instead of one "duty" moderator?
       There is no on-call concept yet; a single inbox becomes a single
       point of failure if that one person is away.
    3. Why cap at 50 notified users? A huge staff roster should still
       work; unbounded notification fan-out on a spam request is the
       resource-exhaustion the rate limit cannot fully see.
    4. Why dedupe open reports from the same user/project inside a 24h
       window? The rate limit bounds *requests* per IP, not a determined
       spammer who rotates IPs; one open report stands for "the community
       flagged this", and a queue full of near-identical rows makes the
       worst reports harder to find.
    5. Why create the row first, then notify? The notification is a
       side effect; the row is the durable truth. If notification fails
       for any reason, the report still exists and the queue still shows
       it the next time a moderator opens the triage page.
    """
    authenticated = bool(user is not None and getattr(user, 'is_authenticated', False))
    if authenticated:
        since = timezone.now() - timedelta(hours=cooldown_hours)
        existing = (
            AppReport.objects
            .filter(project=project, user=user, status='open', created_at__gte=since)
            .order_by('-created_at')
            .first()
        )
        if existing is not None:
            return existing, False

    report = AppReport.objects.create(
        project=project,
        user=user if authenticated else None,
        reason=reason,
        details=(details or '')[:500],
        status='open',
        outcome='',
    )
    try:
        base = f'Report: “{project.title}” — {report.get_reason_display()}'
        target_url = project.get_absolute_url()
        reported_by = f'Reported by @{user.username}' if (user is not None and user.is_authenticated) else 'Reported anonymously'
        body = (report.details or reported_by)[:400]
        for staff in moderators_to_notify(user):
            notify(staff, 'report', base, body, target_url)
    except Exception:
        logger.exception('report notify failed slug=%s id=%s', project.slug, report.pk)
    return report, True


def resolve_report(report: AppReport, actor, decision: str, note: str = '') -> dict:
    """Resolve one report under a lock, returning a structured result.

    Decision → outcome:
      - ignore      → no_action        (no violation was found)
      - quarantine  → quarantined      (unpublished until reviewed)
      - remove      → removed/deleted  (lifecycle decides, money-aware)
      - delete      → deleted/removed  (lifecycle decides, money-aware)

    Returns {'ok': bool, 'message': str, 'outcome': ...}.
    """
    if not _is_moderator(actor):
        return {'ok': False, 'message': 'You do not have permission to moderate reports.'}

    if decision not in VALID_DECISIONS:
        return {'ok': False, 'message': 'Unknown moderation decision.'}

    if decision in ('remove', 'delete') and not _is_admin(actor):
        return {'ok': False, 'message': 'Removing a vibe requires an admin.'}

    try:
        with transaction.atomic():
            locked = AppReport.objects.select_for_update().get(pk=report.pk)
            if locked.status != 'open':
                return {
                    'ok': False,
                    'message': f'This report was already {locked.status.lower()} by @{locked.handled_by.username if locked.handled_by else "someone"}.',
                    'outcome': locked.outcome,
                }
            # Capture the project object once, under a row lock. A hard
            # delete will cascade the report rows away, so we never
            # re-query it afterwards; the in-memory object keeps
            # slug/owner enough for audit + notification after the project
            # row is gone. Locking prevents a concurrent owner edit from
            # racing our quarantine (or a concurrent owner delete from
            # racing our admin action).
            project = AppProject.objects.select_for_update().select_related('owner').get(pk=locked.project_id)

            # Resolve the content first: what happens to the vibe decides
            # what the report "outcome" truly was (a "remove" on an unpaid
            # vibe still hard-deletes, because no receipt needs keeping).
            project_message = ''
            outcome = OUTCOME_BY_DECISION[decision]
            if decision in ('quarantine', 'remove', 'delete'):
                action = _apply_project_action(project, actor, decision, locked)
                project_message = action['message']
                outcome = action['outcome']

            locked.status = 'ignored' if decision == 'ignore' else 'resolved'
            locked.outcome = outcome
            locked.handled_by = actor
            locked.handled_at = timezone.now()
            locked.note = (note or '')[:500]
            # A hard delete cascades the report row (and all its siblings);
            # saving after a cascade would be a no-op UPDATE that can never
            # read back. Skip the write when the project row is already gone.
            if AppProject.objects.filter(pk=project.pk).exists():
                locked.save(update_fields=['status', 'outcome', 'handled_by', 'handled_at', 'note'])

            # A moderating action on a vibe answers every open report about
            # that vibe; leaving them "open" makes the queue lie after the
            # content is already gone (or held). Do it inside the same
            # transaction so the queue never shows a half-resolved state.
            # On a hard delete the cascade already removed them; the update
            # is a no-op, which is honest.
            _autoresolve_open_siblings(project, actor, outcome, note)

            from users.models import AdminLog
            AdminLog.objects.create(
                actor=actor,
                action='report_' + decision,
                target=project.slug,
            )
    except Exception as exc:
        logger.exception('resolve report failed id=%s', report.pk)
        return {'ok': False, 'message': 'Could not resolve the report. Try again.'}

    action_label = dict(OUTCOME_BY_DECISION).get(decision, decision)
    return {
        'ok': True,
        'message': f'Report resolved as “{action_label}”.{project_message}',
        'outcome': outcome,
    }


def _apply_project_action(project: AppProject, actor, decision: str, report: AppReport) -> dict:
    """Apply the content change a report resolution implies.

    Returns {'outcome': actual_outcome, 'message': user-facing sentence}.

    5 Whys:
    1. Why is quarantine software-set trust reset? A trust badge is a
       verdict about the bytes currently published. Quarantining is a
       state change; a badge that still says "verified" for a vibe the
       platform itself has pulled is a contradiction a screenshot can
       prove.
    2. Why do remove and delete both call remove_project? The money rule
       (paid vibe → keep buyers' receipts) must be identical everywhere;
       lifecycle owns it because moderation and account deletion both need
       it.
    3. Why rewrite the ScanJob to quarantined on quarantine? The scan
       queue is the only place a user can see "why is this held" after a
       manual quarantine; writing it here gives them an answer after the
       human decision, not just after an automated one.
    4. Why notify the owner before the project row can disappear? A hard
       delete cascades the row; notifying after would query an owner that
       no longer exists. Capture the user, deliver the message, then let
       lifecycle do its money-aware work.
    5. Why never notify the project owner on ignore? Nothing changed about
       their content; emailing "your report was dismissed" would teach
       people to spam the system for attention.
    """
    owner = project.owner  # in-memory; safe to read after a hard delete

    if decision == 'quarantine':
        AppProject.objects.filter(pk=project.pk).update(
            status='quarantined',
            trust='',
            trust_graded_at=None,
        )
        from .models import ScanJob
        ScanJob.objects.update_or_create(project=project, defaults={'status': 'quarantined'})
        notify(
            owner,
            'report',
            f'“{project.title}” was quarantined after a report',
            'A moderator held this vibe until it is reviewed. Edit and re-upload when it is clean.',
            project.get_absolute_url(),
        )
        return {'outcome': 'quarantined', 'message': ' The vibe is now quarantined.'}

    # Tell the owner before the content may be deleted; remove_project can
    # cascade the row and the project object as an ORM query.
    notify(
        owner,
        'report',
        f'“{project.title}” was removed after a report',
        'Moderators removed this vibe. If this is a mistake, contact support with your receipt where applicable.',
        '/my-vibes/',
    )
    outcome = remove_project(project)  # 'deleted' or 'removed'
    return {
        'outcome': 'removed' if outcome == 'removed' else 'deleted',
        'message': f' The vibe was {"removed" if outcome == "removed" else "deleted"}.',
    }


def _autoresolve_open_siblings(project: AppProject, actor, outcome: str, note: str) -> int:
    """Resolve every other open report about the same project."""
    open_siblings = AppReport.objects.select_for_update().filter(
        project=project,
        status='open',
    )
    return open_siblings.update(
        status='resolved',
        outcome=outcome,
        handled_by=actor,
        handled_at=timezone.now(),
        note=(note or 'Auto-resolved with a report on the same vibe.')[:500],
    )
