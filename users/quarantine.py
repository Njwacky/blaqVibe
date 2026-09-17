"""Account quarantine — write once, notify everyone, allow an appeal.

WHAT THIS IS FOR
The public-language gate (`gallery/profanity.py`) already refuses offensive
text. It refused it *silently*: the form said "Please reword this", the person
was never told their account had been recorded, no staff member ever saw it,
and a repeat offender was indistinguishable from someone who fat-fingered a
slur once. This module is the missing half: a violation is recorded, the
person is told (in line, in their inbox, and by email), the account is held
for 30 days, staff are notified, and the person can appeal the decision.

THE RULES, IN ONE PLACE
* One violation (default) → 30-day quarantine. Both numbers are settings:
  `QUARANTINE_STRIKES` and `QUARANTINE_DAYS`. The strike count still counts
  violations inside a 90-day window, so an operator can raise the threshold
  without a schema change.
* A quarantined account may still READ, download, trade and spend. It may not
  post NEW public content (publish, comment, review, PR, profile text, git
  push). The decorator `users.decorators.not_quarantined` enforces that at
  the write views; `git_daemon` enforces it for pushes.
* The hold ends by itself when the clock passes `ends_at` (no cron needed).
  Reads of `active_quarantine()` treat a past `ends_at` as over.
* Everything the person needs to know is on /quarantine/ — what happened,
  when it lifts, and the appeal box.
* Every action is audited: RuleViolation (what happened), UserQuarantine
  (the hold), QuarantineAppeal (what the person said, and the decision).

WHO WRITES WHAT
`quarantine_user()` is the only creator of a UserQuarantine, `record_violation()`
is the only creator of a RuleViolation, and `resolve_appeal()` is the only
writer of an appeal decision. Staff views call these; they never write the
tables themselves.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import QuarantineAppeal, RuleViolation, UserQuarantine

logger = logging.getLogger(__name__)

# Defaults an operator can override in settings (env-driven in blaqvibes/settings.py).
DEFAULT_QUARANTINE_DAYS = 30
DEFAULT_STRIKES = 1
VIOLATION_WINDOW_DAYS = 90
APPEAL_COOLDOWN_HOURS = 24
MAX_EVIDENCE = 600
MAX_APPEAL_LENGTH = 2000
DUPLICATE_WINDOW_MINUTES = 30

# Human labels for where a violation happened. A slug the view passes that is
# not here still works — it is just shown as-is.
SURFACE_LABELS = {
    'comment': 'a comment',
    'review': 'a review',
    'pr': 'a pull request',
    'project': 'a project (title, description or README)',
    'profile': 'a profile field',
    'username': 'a username',
    'tip': 'a tip message',
    'skill': 'a skill',
    'ai_readme': 'a generated README',
    'appeal': 'an appeal',
    'staff': 'a staff action',
}

def quarantine_days() -> int:
    try:
        return max(1, int(getattr(settings, 'QUARANTINE_DAYS', DEFAULT_QUARANTINE_DAYS)))
    except Exception:
        return DEFAULT_QUARANTINE_DAYS

def strikes_to_quarantine() -> int:
    try:
        return max(1, int(getattr(settings, 'QUARANTINE_STRIKES', DEFAULT_STRIKES)))
    except Exception:
        return DEFAULT_STRIKES

def _surface_label(surface: str) -> str:
    return SURFACE_LABELS.get(surface or '', surface or 'the site')

def _clip(value, limit=MAX_EVIDENCE) -> str:
    return (value or '').strip()[:limit]

def _audit(actor, action, target, detail=''):
    """One AdminLog row per staff decision — the account of record."""
    if actor is None or not getattr(actor, 'is_authenticated', False):
        return
    try:
        from .models import AdminLog
        AdminLog.objects.create(
            actor=actor,
            action=action,
            target=f'@{getattr(target, "username", "?")}: {detail}'[:200],
        )
    except Exception:
        logger.exception('quarantine audit row failed action=%s', action)

# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def active_quarantine(user, *, now=None):
    """The live hold on `user`, or None. Anonymous users are never held.

    Expiry is decided by the clock here, so a leftover 'active' row whose
    `ends_at` has passed stops blocking immediately — `sweep_expired()` is
    only there to tidy the status and tell the person it is over.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    now = now or timezone.now()
    try:
        return (
            UserQuarantine.objects
            .filter(user=user, status='active', ends_at__gt=now)
            .order_by('-ends_at')
            .first()
        )
    except Exception:
        # A broken read must not block a request, and must not let a hold
        # silently disappear either: log loudly and treat as not held (the
        # write paths re-check through the DB anyway).
        logger.exception('active_quarantine read failed user=%s', getattr(user, 'pk', None))
        return None

def is_quarantined(user) -> bool:
    return active_quarantine(user) is not None

def latest_quarantine(user):
    """The most recent hold, active or not — used by the person's own page."""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    try:
        return UserQuarantine.objects.filter(user=user).order_by('-started_at').first()
    except Exception:
        logger.exception('latest_quarantine read failed user=%s', getattr(user, 'pk', None))
        return None

def strikes_in_window(user, *, now=None) -> int:
    """Rule violations in the last 90 days — the number the threshold reads."""
    now = now or timezone.now()
    since = now - timedelta(days=VIOLATION_WINDOW_DAYS)
    try:
        return RuleViolation.objects.filter(user=user, created_at__gte=since).count()
    except Exception:
        logger.exception('strikes_in_window failed user=%s', getattr(user, 'pk', None))
        return 0

def open_appeal(quarantine):
    if not quarantine:
        return None
    return quarantine.appeals.filter(status='open').order_by('-created_at').first()

def last_appeal(quarantine):
    if not quarantine:
        return None
    return quarantine.appeals.order_by('-created_at').first()

def can_appeal(quarantine, *, now=None) -> tuple[bool, str]:
    """(allowed, why-not). One open appeal at a time; a cooling-off after one."""
    now = now or timezone.now()
    if not quarantine or not quarantine.is_active(now):
        return False, 'This quarantine is not active.'
    if open_appeal(quarantine):
        return False, 'Your appeal is already with the moderators — you will get an answer in your inbox.'
    latest = last_appeal(quarantine)
    if latest is not None:
        wait_until = latest.created_at + timedelta(hours=APPEAL_COOLDOWN_HOURS)
        if wait_until > now:
            hours = max(1, int((wait_until - now).total_seconds() // 3600) + 1)
            return False, f'You can send another appeal in about {hours} hour(s).'
    return True, ''

def quarantine_block_message(quarantine) -> str:
    """The sentence a blocked write shows the person. Never quotes the slur."""
    if not quarantine:
        return ''
    return (
        f'Your account is quarantined until {quarantine.ends_label()} '
        f'({quarantine.reason_label.lower()}). You can read, download and trade, '
        f'but new public posts are paused. Think this was a misunderstanding? '
        f'Appeal on /quarantine/ — a moderator reads every appeal.'
    )

def quarantine_notice_url() -> str:
    try:
        return reverse('quarantine_notice')
    except Exception:
        return '/quarantine/'

# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

@transaction.atomic
def quarantine_user(user, *, reason='other', detail='', days=None, imposed_by=None,
                    source='auto', strike_count=None):
    """Apply (or extend) a 30-day hold on `user`. Returns the UserQuarantine.

    A second hold on an already-quarantined account does NOT stack silently:
    the existing row is extended by `days` and its detail records why, so the
    person's page shows ONE end date instead of three overlapping ones.
    """
    days = int(days or quarantine_days())
    now = timezone.now()
    live = (
        UserQuarantine.objects
        .select_for_update()
        .filter(user=user, status='active', ends_at__gt=now)
        .order_by('-ends_at')
        .first()
    )
    if live is not None:
        live.ends_at = live.ends_at + timedelta(days=days)
        live.days = days
        if strike_count is not None:
            live.strike_count = strike_count
        if detail:
            live.detail = (f'{live.detail} | {detail}'.strip(' |'))[:300]
        live.save(update_fields=['ends_at', 'days', 'strike_count', 'detail'])
        _audit(imposed_by, 'quarantine_user', user, f'{live.reason_label} extended to {live.ends_label()}')
        _notify_user_extended(live)
        _fan_out_staff(live, extended=True)
        return live

    quarantine = UserQuarantine.objects.create(
        user=user,
        reason=reason if reason in dict(RuleViolation.KINDS) else 'other',
        detail=_clip(detail, 300),
        status='active',
        source=source if source in ('auto', 'staff') else 'auto',
        days=days,
        strike_count=int(strike_count or strikes_in_window(user) or 1),
        imposed_by=imposed_by,
        ends_at=now + timedelta(days=days),
    )
    _audit(imposed_by, 'quarantine_user', user,
           f'{quarantine.reason_label} until {quarantine.ends_label()}')
    _notify_user(quarantine)
    _fan_out_staff(quarantine)
    return quarantine

@transaction.atomic
def record_violation(user, *, kind='other', surface='', detail='', evidence='',
                     project_slug='', quarantine_after=None, days=None,
                     source='auto', imposed_by=None) -> dict:
    """Record one rule breach and apply the policy. Returns a small report.

    The returned dict is what a view uses to tell the person what just
    happened:
        {'violation', 'strikes', 'quarantined', 'quarantine', 'threshold'}
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return {'violation': None, 'strikes': 0, 'quarantined': False,
                'quarantine': None, 'threshold': strikes_to_quarantine()}

    kind = kind if kind in dict(RuleViolation.KINDS) else 'other'
    evidence = _clip(evidence)
    now = timezone.now()

    # A double-submit (or a form re-posted after an error) must not count as
    # two violations: same user, same text, inside half an hour is one event.
    duplicate = None
    if evidence:
        duplicate = (
            RuleViolation.objects
            .filter(user=user, evidence=evidence, created_at__gte=now - timedelta(minutes=DUPLICATE_WINDOW_MINUTES))
            .order_by('-created_at')
            .first()
        )
    if duplicate is not None:
        live = active_quarantine(user, now=now)
        return {
            'violation': duplicate,
            'strikes': strikes_in_window(user, now=now),
            'quarantined': bool(live),
            'quarantine': live,
            'threshold': strikes_to_quarantine(),
            'deduplicated': True,
        }

    threshold = int(quarantine_after or strikes_to_quarantine())
    violation = RuleViolation.objects.create(
        user=user,
        kind=kind,
        surface=_clip(surface, 24),
        detail=_clip(detail, 300),
        evidence=evidence,
        project_slug=_clip(project_slug, 120),
    )
    strikes = strikes_in_window(user, now=now)

    live = active_quarantine(user, now=now)
    if live is not None:
        # Already held: record the breach, keep the same end date. Stacking
        # 30 days per angry retry turns one bad afternoon into a year, and
        # staff can extend deliberately from the appeals queue if needed.
        violation.quarantined = False
        violation.save(update_fields=['quarantined'])
        _tell_person_already_held(violation, live)
        _fan_out_staff(live, violation=violation)
        return {'violation': violation, 'strikes': strikes, 'quarantined': True,
                'quarantine': live, 'threshold': threshold}

    if strikes >= threshold:
        quarantine = quarantine_user(
            user,
            reason=kind,
            detail=f'{_surface_label(surface)}: {detail}'.strip(': '),
            days=days,
            source=source,
            imposed_by=imposed_by,
            strike_count=strikes,
        )
        violation.quarantined = True
        violation.save(update_fields=['quarantined'])
        return {'violation': violation, 'strikes': strikes, 'quarantined': True,
                'quarantine': quarantine, 'threshold': threshold}

    # Under the threshold (only possible when an operator raises the strikes
    # setting): the person still gets told, so a "warning only" policy is
    # never silent either.
    _tell_person_warned(violation, strikes, threshold)
    return {'violation': violation, 'strikes': strikes, 'quarantined': False,
            'quarantine': None, 'threshold': threshold}

def lift_quarantine(quarantine, *, actor=None, note='', notify_user=True):
    """End a hold early. Staff action; the person is always told."""
    if quarantine is None:
        return None
    if quarantine.status != 'active':
        return quarantine
    quarantine.status = 'lifted'
    quarantine.lifted_at = timezone.now()
    quarantine.lifted_by = actor if getattr(actor, 'is_authenticated', False) else None
    quarantine.lift_note = _clip(note, 300)
    quarantine.save(update_fields=['status', 'lifted_at', 'lifted_by', 'lift_note'])
    _audit(actor, 'quarantine_lifted', quarantine.user, note or 'lifted early')
    if notify_user:
        _tell_person_lifted(quarantine)
    return quarantine

def extend_quarantine(quarantine, *, actor=None, days=None, note=''):
    """Add time to a live hold (staff decision on a bad-faith appeal)."""
    if not quarantine or not quarantine.is_active():
        return quarantine
    days = int(days or quarantine_days())
    quarantine.ends_at = quarantine.ends_at + timedelta(days=days)
    quarantine.days = days
    if note:
        quarantine.detail = (f'{quarantine.detail} | {note}'.strip(' |'))[:300]
    quarantine.save(update_fields=['ends_at', 'days', 'detail'])
    _audit(actor, 'quarantine_extended', quarantine.user, f'now until {quarantine.ends_label()}')
    _tell_person_extended(quarantine)
    return quarantine

def sweep_expired(*, notify=True, limit=200) -> int:
    """Close out holds whose time is up and tell the people they can post.

    Called by the staff queue and the person's own notice page — both are
    places a human is looking at the state, so the tidy-up rides along with
    a real request instead of needing a cron job.
    """
    now = timezone.now()
    expired = list(
        UserQuarantine.objects
        .filter(status='active', ends_at__lte=now)
        .order_by('ends_at')[:limit]
    )
    for quarantine in expired:
        quarantine.status = 'expired'
        quarantine.save(update_fields=['status'])
        if notify:
            _tell_person_expired(quarantine)
    return len(expired)

# ---------------------------------------------------------------------------
# Profanity hook — ONE call site shape for every view that refuses text
# ---------------------------------------------------------------------------

def form_has_language_violation(form) -> bool:
    """True when a form's errors include the public-language refusal.

    `form.errors.as_data()` keeps the original ValidationError objects, so we
    match the exception TYPE (gallery.profanity.PublicLanguageError) instead of
    string-matching the human message.
    """
    from gallery.profanity import PublicLanguageError
    try:
        for errors in form.errors.as_data().values():
            for error in errors:
                if isinstance(error, PublicLanguageError):
                    return True
    except Exception:
        logger.exception('form_has_language_violation failed')
    return False

def _blocked_field_names(form) -> list[str]:
    from gallery.profanity import PublicLanguageError
    names = []
    try:
        for name, errors in form.errors.as_data().items():
            if any(isinstance(error, PublicLanguageError) for error in errors):
                names.append(name)
    except Exception:
        pass
    return names

def _blocked_text(form) -> str:
    """The refused text itself — what staff need for an appeal."""
    for name in _blocked_field_names(form):
        try:
            value = form.data.get(name)
        except Exception:
            value = None
        if value:
            return str(value)
    return ''

def note_blocked_language(request, *, surface, form=None, text=None, project=None,
                          kind='offensive_language', detail='') -> dict | None:
    """A view's profanity gate just refused text. Record it and tell the person.

    Returns the `record_violation()` report (or None when there was nothing to
    record: anonymous visitor, or a form that failed for some other reason).
    The person gets an in-line message here, plus the inbox notification and
    email that `record_violation()` sends — so a blocked post is never silent.
    """
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    if form is not None and not form_has_language_violation(form):
        return None
    evidence = text if text is not None else (_blocked_text(form) if form is not None else '')
    if not (evidence or '').strip() and not detail:
        return None
    slug = ''
    if project is not None:
        slug = getattr(project, 'slug', '') or ''
    try:
        report = record_violation(
            user,
            kind=kind,
            surface=surface,
            detail=detail or f'Text refused by the public-language gate in {_surface_label(surface)}.',
            evidence=evidence,
            project_slug=slug,
        )
    except Exception:
        # Never turn a rule breach into a 500 for the person. The text was
        # already refused; the trail can be repaired from the logs.
        logger.exception('note_blocked_language failed user=%s surface=%s', user.pk, surface)
        return None

    try:
        if report.get('quarantined') and report.get('quarantine'):
            messages.error(request, quarantine_block_message(report['quarantine']))
        else:
            messages.warning(
                request,
                'That text breaks the public rules and was not posted. '
                f'One more within {VIOLATION_WINDOW_DAYS} days quarantines the account.'
                if report.get('threshold', 1) > report.get('strikes', 0)
                else 'That text breaks the public rules and was not posted.',
            )
    except Exception:
        pass
    return report

# ---------------------------------------------------------------------------
# Appeals
# ---------------------------------------------------------------------------

def submit_appeal(quarantine, message) -> tuple[QuarantineAppeal | None, str]:
    """File one appeal. Returns (appeal, error_message)."""
    allowed, why = can_appeal(quarantine)
    if not allowed:
        return None, why
    message = (message or '').strip()[:MAX_APPEAL_LENGTH]
    if len(message) < 10:
        return None, 'Tell us what actually happened (at least 10 characters) — one line is not enough to review.'
    appeal = QuarantineAppeal.objects.create(
        quarantine=quarantine,
        user=quarantine.user,
        message=message,
        status='open',
    )
    _notify_user_appeal_received(appeal)
    _fan_out_staff_appeal(appeal)
    return appeal, ''

def _notify_user_appeal_received(appeal):
    _notify(
        appeal.user,
        'appeal',
        'Appeal received — a moderator will review it',
        (
            'Your appeal is with the moderation team. You will get an answer here in '
            'your inbox (and by email) either way, and you can keep reading and '
            'downloading while you wait.'
        ),
        quarantine_notice_url(),
    )


def resolve_appeal(appeal, *, actor, decision, note='') -> dict:
    """Apply a staff decision to one appeal. Returns {'ok', 'message', 'decision'}."""
    if appeal is None:
        return {'ok': False, 'message': 'That appeal no longer exists.', 'decision': ''}
    if decision not in dict(QuarantineAppeal.DECISIONS):
        return {'ok': False, 'message': 'Unknown decision.', 'decision': ''}
    if appeal.status != 'open':
        return {'ok': False, 'message': 'That appeal has already been reviewed.', 'decision': ''}
    note = _clip(note, 400)

    if decision == 'accept':
        appeal.status = 'accepted'
        lift_quarantine(appeal.quarantine, actor=actor,
                        note=note or 'Appeal accepted — the quarantine was lifted.')
        outcome = 'accepted'
        message = f'Appeal accepted — @{appeal.user.username} can post again immediately.'
    elif decision == 'extend':
        appeal.status = 'denied'
        extend_quarantine(appeal.quarantine, actor=actor, note=note or 'Appeal reviewed — hold extended.')
        outcome = 'extended'
        message = f'Appeal denied and 30 days added — @{appeal.user.username} is now held until {appeal.quarantine.ends_label()}.'
    else:
        appeal.status = 'denied'
        outcome = 'denied'
        message = f'Appeal denied — the quarantine stands until {appeal.quarantine.ends_label()}.'

    appeal.reviewed_by = actor if getattr(actor, 'is_authenticated', False) else None
    appeal.reviewed_at = timezone.now()
    appeal.decision_note = note
    appeal.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'decision_note'])
    _audit(actor, 'quarantine_appeal', appeal.user, f'{outcome} — {note or "no note"}')
    _tell_person_decision(appeal)
    return {'ok': True, 'message': message, 'decision': outcome}

# ---------------------------------------------------------------------------
# Notifications — the person, then the staff
# ---------------------------------------------------------------------------

def _notify(user, kind, title, body, url):
    from gallery.notify import notify
    try:
        return notify(user, kind, title, body, url)
    except Exception:
        logger.exception('quarantine notify failed user=%s kind=%s', getattr(user, 'pk', None), kind)
        return None

def _email_user(user, subject, context):
    """Account-critical email: not subject to marketing toggles."""
    if user is None or not getattr(user, 'email', None):
        return False
    try:
        from django.template.loader import render_to_string
        from .emails import send_generic_email
        ctx = {'user': user, 'site_url': getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za').rstrip('/'), **context}
        try:
            text_body = render_to_string('emails/quarantine_notice.txt', ctx)
        except Exception:
            text_body = (
                f'Hi @{user.username},\n\n{ctx.get("headline", subject)}\n\n'
                f'Details: {ctx.get("reason", "")}\n'
                f'Your notice and appeal form: {ctx["site_url"]}{quarantine_notice_url()}\n\n'
                f'— BlaqVibes\n'
            )
        try:
            html_body = render_to_string('emails/quarantine_notice.html', ctx)
        except Exception:
            html_body = None
        return send_generic_email(user.email, subject, text_body, html_body)
    except Exception:
        logger.exception('quarantine email failed user=%s', getattr(user, 'pk', None))
        return False

def _notify_user(quarantine):
    url = quarantine_notice_url()
    title = f'Your account is quarantined for {quarantine.days} days'
    body = (
        f'Reason: {quarantine.reason_label}. You can still read and download — '
        f'posting is paused until {quarantine.ends_label()}. If this was a '
        f'misunderstanding, appeal and a moderator will read it.'
    )
    _notify(quarantine.user, 'account_quarantine', title, body, url)
    _email_user(quarantine.user, 'Your BlaqVibes account is quarantined', {
        'quarantine': quarantine,
        'headline': title,
        'reason': quarantine.reason_label,
        'ends_at': quarantine.ends_label(),
        'extended': False,
    })

def _notify_user_extended(quarantine):
    url = quarantine_notice_url()
    title = f'Your quarantine was extended — until {quarantine.ends_label()}'
    body = (
        f'A new rule breach was recorded while your account is quarantined. '
        f'Posting stays paused until {quarantine.ends_label()}.'
    )
    _notify(quarantine.user, 'account_quarantine', title, body, url)

def _tell_person_already_held(violation, quarantine):
    """A breach while already held: tell them, don't stack time."""
    _notify(
        quarantine.user,
        'account_quarantine',
        'Another rule breach was recorded on your quarantined account',
        (
            f'Your account stays quarantined until {quarantine.ends_label()}. '
            f'Further breaches are kept on record and a moderator can extend the hold. '
            f'Appeal if this was a misunderstanding.'
        ),
        quarantine_notice_url(),
    )

def _tell_person_warned(violation, strikes, threshold):
    """Under a raised threshold: a warning, said out loud."""
    _notify(
        violation.user,
        'account_quarantine',
        'Warning — that text breaks the public rules',
        (
            f'Your {_surface_label(violation.surface)} was not posted. '
            f'{strikes} of {threshold} violations recorded in the last '
            f'{VIOLATION_WINDOW_DAYS} days — the next one quarantines the account for '
            f'{quarantine_days()} days.'
        ),
        quarantine_notice_url(),
    )

def _tell_person_lifted(quarantine):
    _notify(
        quarantine.user,
        'account_quarantine',
        'Your quarantine was lifted — you can post again',
        quarantine.lift_note or 'A moderator reviewed your account and lifted the quarantine.',
        quarantine_notice_url(),
    )
    _email_user(quarantine.user, 'Your BlaqVibes quarantine was lifted', {
        'quarantine': quarantine,
        'headline': 'Your quarantine was lifted — you can post again.',
        'reason': quarantine.lift_note or 'A moderator reviewed your account.',
        'ends_at': '',
        'lifted': True,
    })

def _tell_person_extended(quarantine):
    _notify(
        quarantine.user,
        'account_quarantine',
        f'Your quarantine now runs until {quarantine.ends_label()}',
        quarantine.detail or 'A moderator decided the hold should run longer.',
        quarantine_notice_url(),
    )

def _tell_person_expired(quarantine):
    _notify(
        quarantine.user,
        'account_quarantine',
        'Your quarantine has ended — welcome back',
        'The 30-day hold is over. Posting is open again — please keep it civil.',
        quarantine_notice_url(),
    )

def _tell_person_decision(appeal):
    url = quarantine_notice_url()
    if appeal.status == 'accepted':
        _notify(
            appeal.user,
            'appeal',
            'Your appeal was accepted — the quarantine is lifted',
            appeal.decision_note or 'A moderator agreed this was a misunderstanding. Posting is open again.',
            url,
        )
        _email_user(appeal.user, 'Your BlaqVibes appeal was accepted', {
            'quarantine': appeal.quarantine,
            'headline': 'Your appeal was accepted — the quarantine is lifted.',
            'reason': appeal.decision_note or 'A moderator agreed this was a misunderstanding.',
            'ends_at': '',
            'lifted': True,
        })
    elif appeal.status == 'denied':
        extended = 'extend' in (appeal.decision_note or '').lower()
        ends = appeal.quarantine.ends_label() if appeal.quarantine.is_active() else ''
        body = appeal.decision_note or 'A moderator reviewed the evidence and the quarantine stands.'
        if ends:
            body = f'{body} Posting stays paused until {ends}.'
        _notify(appeal.user, 'appeal', 'Your appeal was reviewed — the quarantine stands', body, url)
        _email_user(appeal.user, 'Your BlaqVibes appeal was reviewed', {
            'quarantine': appeal.quarantine,
            'headline': 'Your appeal was reviewed — the quarantine stands.',
            'reason': appeal.decision_note or 'A moderator reviewed the evidence.',
            'ends_at': ends,
            'extended': extended,
        })

def _fan_out_staff(quarantine, *, violation=None, extended=False, appeal=None):
    """In-app + email to every moderator/admin. Never includes the slur."""
    try:
        from gallery.admin_notifications import notify_admins_for_approval
    except Exception:
        logger.exception('could not import admin notification fan-out')
        return
    url = '/moderation/appeals/'
    headline = (
        f'Quarantine {"extended" if extended else "applied"}: @{quarantine.user.username}'
    )
    lines = [
        f'@{quarantine.user.username} ({quarantine.reason_label.lower()}) is held until {quarantine.ends_label()}.',
        f'Violations on record: {quarantine.strike_count}.',
    ]
    if violation is not None and violation.surface:
        lines.append(f'Latest breach: {_surface_label(violation.surface)}.')
    if appeal is not None:
        lines.append('The person has filed an appeal — review it on the appeals page.')
    body = ' '.join(lines)
    try:
        notify_admins_for_approval(
            kind='account_quarantine',
            title=headline,
            body=body,
            url=url,
            email_subject=f'[Quarantine] @{quarantine.user.username} — {quarantine.reason_label}',
            email_text_template='emails/admin_user_quarantine.txt',
            email_html_template='emails/admin_user_quarantine.html',
            context={
                'quarantine': quarantine,
                'target': quarantine.user,
                'violation': violation,
                'appeal': appeal,
                'appeals_url': url,
                'notice_url': quarantine_notice_url(),
            },
        )
    except Exception:
        logger.exception('staff quarantine fan-out failed user=%s', quarantine.user_id)

def _fan_out_staff_appeal(appeal):
    """An appeal is a person asking for a human — staff must see it at once."""
    try:
        from gallery.admin_notifications import notify_admins_for_approval
    except Exception:
        logger.exception('could not import admin notification fan-out')
        return
    url = '/moderation/appeals/'
    try:
        notify_admins_for_approval(
            kind='appeal',
            title=f'Appeal from @{appeal.user.username} — {appeal.quarantine.reason_label}',
            body=(
                f'@{appeal.user.username} says this was a misunderstanding. '
                f'Quarantine runs until {appeal.quarantine.ends_label()}. '
                f'Open the appeals queue to accept, deny or extend.'
            ),
            url=url,
            email_subject=f'[Appeal] @{appeal.user.username} appealed their quarantine',
            email_text_template='emails/admin_appeal.txt',
            email_html_template='emails/admin_appeal.html',
            context={
                'appeal': appeal,
                'quarantine': appeal.quarantine,
                'target': appeal.user,
                'appeals_url': url,
                'notice_url': quarantine_notice_url(),
            },
        )
    except Exception:
        logger.exception('staff appeal fan-out failed appeal=%s', getattr(appeal, 'pk', None))
