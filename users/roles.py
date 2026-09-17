"""Role changes — ONE writer, ONE trail.

`apply_role_change()` is the only code path in the app that may move a
`Profile.role`. Views call it, it decides, it audits. Nothing else writes the
field (grep for `role =` — `users/roles.py` should be the only app write).

5 Whys — why not let each admin view set `profile.role` and save it?

1. Why a module? Two views already needed the same write, and every future
   admin action (ban, grant, bulk CSV import) will need it too. Copies drift:
   one forgets the audit row, another forgets the staff sync.
2. Why guards inside? A UI guard is a promise; a server guard is a fact. The
   "last superadmin" check has to exist somewhere a crafted POST cannot skip,
   and it has to be counted inside the same transaction as the write.
3. Why a reason? At 10k+ users nobody remembers in March why @thando became
   admin in January. `AdminLog` is the account of record for access reviews
   (SOC2 / any "who can touch production?" question) — a log of "changed role"
   with no why is unusable for that.
4. Why typed confirmation on a promotion only? Granting power is the dangerous
   direction and it is rare (a handful per year), so a two-second deliberate
   action is cheap. Removing power is the fail-safe direction — friction there
   makes people delay a demotion they should just do.
5. Why notify the target? A silent privilege change is how an insider quietly
   keeps access nobody knows they have. The person affected is the one human
   guaranteed to notice a change that should not have happened.

Two axes, deliberately separate (this is the part that confuses people):

* `Profile.role` (user → moderator → admin → superadmin) governs BlaqVibes'
  own pages: /admin/dashboard/, /admin/roles/, moderation, reports.
* Django's `is_staff` / `is_superuser` govern /blaq-admin-secure/ (the Django
  admin) and Django groups.

We keep them coherent for the roles the app owns (admin/superadmin ⇒ is_staff
True, plain user/moderator ⇒ False), but we NEVER silently clear
`is_superuser`: demoting someone here must not look like it revoked the Django
admin account when it did not. When the target still holds that flag, the
result says so out loud and the audit row records it.
"""
import logging
from dataclasses import dataclass

from django.contrib.auth.models import User
from django.db import transaction
from django.urls import reverse

from .models import AdminLog, Profile

logger = logging.getLogger(__name__)

# Low → high. The single ordering used by decorators, guards and templates.
ROLE_ORDER = {'user': 0, 'moderator': 1, 'admin': 2, 'superadmin': 3}

# Shown on the role picker. Written for an operator deciding, not for a
# developer reading code — "what does this let them do to other people?"
ROLE_GUIDE = (
    ('user', 'User', 'Publish, comment, trade. No moderation tools, no admin pages.'),
    ('moderator', 'Moderator', 'Moderation queue only: approve or quarantine other people\'s projects.'),
    ('admin', 'Admin', 'Moderation queue, delete any project, triage reports, admin dashboard, footer contacts.'),
    ('superadmin', 'Super Admin', 'Everything above, plus role changes. Only this role can open Manage roles.'),
)

MIN_REASON = 5        # "why" that carries information
MAX_REASON = 120      # AdminLog.target is 200 chars and also carries the change


@dataclass(frozen=True)
class RoleChangeResult:
    """Outcome of one attempt. `changed` is the only thing a caller must read."""
    changed: bool
    level: str          # 'success' | 'info' | 'error' — matches messages.*
    message: str


def _deny(message, *, reason='', audit=None):
    """Refused on purpose. Returned, never raised: a refusal is a normal
    outcome of an admin page, and the caller renders it as a message.

    A refusal that says someone tried to break a rule (`audit` is set) is
    worth a row: an operator who keeps trying to demote themselves, or a
    crafted POST naming a role that does not exist, is a signal an access
    review should be able to see. Ordinary mistakes (no reason yet) are not.
    """
    if reason:
        logger.warning('role change refused: %s', reason)
    if audit is not None:
        actor, target, detail = audit
        try:
            AdminLog.objects.create(
                actor=actor,
                action='set_role_denied',
                target=f'@{target.username}: {detail}'[:200],
            )
        except Exception:
            logger.exception('could not write denial audit row for @%s', target)
    return RoleChangeResult(False, 'error', message)


def superadmin_count():
    return Profile.objects.filter(role='superadmin').count()


def is_superadmin(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    try:
        return getattr(user.profile, 'role', '') == 'superadmin'
    except Exception:
        return False


def clean_reason(reason):
    return ' '.join((reason or '').split())[:MAX_REASON]


def apply_role_change(*, actor, target, new_role, reason='', confirm=''):
    """Move `target` to `new_role`, or explain why not.

    Checks are ordered cheapest-first and all of them run before any write.
    The privileged ones (last superadmin) are re-checked inside the
    transaction with the row locked, because two superadmins demoting each
    other at the same moment must not both succeed.
    """
    if not is_superadmin(actor):
        # Defense in depth: the view carries @superadmin_required, and so does
        # this function. A future endpoint cannot forget the check.
        return _deny('Only a Super Admin can change roles.')

    if new_role not in ROLE_ORDER:
        return _deny(
            'Unknown role.',
            reason=f'{actor} posted role={new_role!r}',
            audit=(actor, target, f'unknown role {new_role!r} refused'),
        )

    reason = clean_reason(reason)
    confirm = (confirm or '').strip().lstrip('@')

    profile, _created = Profile.objects.get_or_create(user=target)
    old_role = profile.role if profile.role in ROLE_ORDER else 'user'

    if new_role == old_role:
        return RoleChangeResult(
            False, 'info',
            f'@{target.username} is already {old_role} — nothing changed.',
        )

    if actor is not None and target.pk == actor.pk:
        return _deny(
            'You cannot change your own role. Ask another Super Admin.',
            reason=f'{actor} tried to self-change to {new_role}',
            audit=(actor, target, f'self-change to {new_role} refused'),
        )

    if len(reason) < MIN_REASON:
        return _deny(
            f'Write why this is happening (at least {MIN_REASON} characters) — '
            f'the reason is part of the audit trail.',
        )

    escalating = ROLE_ORDER[new_role] > ROLE_ORDER[old_role]
    if escalating and confirm != target.username:
        return _deny(
            f'That grants a higher role. Type @{target.username} in the '
            f'confirmation box to prove you mean this person.',
        )

    keep_django_superuser = False
    with transaction.atomic():
        # Re-read with the row locked: `old_role` above is for the messages,
        # the decision below is from the locked row.
        locked = Profile.objects.select_for_update().get(pk=profile.pk)
        if locked.role in ROLE_ORDER:
            old_role = locked.role
        if old_role == new_role:
            return RoleChangeResult(
                False, 'info',
                f'@{target.username} is already {new_role} — nothing changed.',
            )
        if old_role == 'superadmin' and new_role != 'superadmin' and superadmin_count() <= 1:
            return _deny(
                f'@{target.username} is the only Super Admin. Promote someone '
                f'else first, or you lock everyone out of this page.',
                reason=f'{actor} tried to demote the last superadmin',
                audit=(actor, target, f'{old_role}→{new_role} refused (last Super Admin)'),
            )

        locked.role = new_role
        fields = ['role']

        # Keep the Django-admin axis coherent with the app axis, without ever
        # silently revoking a Django superuser (see the module docstring).
        if ROLE_ORDER[new_role] >= ROLE_ORDER['admin']:
            if not target.is_staff:
                target.is_staff = True
                target.save(update_fields=['is_staff'])
        elif target.is_staff:
            if target.is_superuser:
                keep_django_superuser = True
            else:
                target.is_staff = False
                target.save(update_fields=['is_staff'])
        locked.save(update_fields=fields)

        note = ''
        if keep_django_superuser:
            note = (' Note: @%s still has Django superuser (is_superuser=True) '
                    'on /blaq-admin-secure/ — that flag is separate and was not '
                    'touched.' % target.username)
        audit_target = (
            f'@{target.username}: {old_role}→{new_role} — {reason}'
            f'{" [django-superuser kept]" if keep_django_superuser else ""}'
        )[:200]  # AdminLog.target is 200 chars — cut here, once, explicitly
        AdminLog.objects.create(actor=actor, action='set_role', target=audit_target)

    # After the commit, never inside it: a rolled-back change must not have
    # mailed anybody, and a broken mail server must not undo a valid change.
    transaction.on_commit(lambda: _announce(actor, target, old_role, new_role, reason))

    label = dict((slug, name) for slug, name, _what in ROLE_GUIDE)
    message = (f'@{target.username}: {old_role} → {new_role} '
               f'({label.get(new_role, new_role)}). Logged.{note}')
    return RoleChangeResult(True, 'success', message)


def _announce(actor, target, old_role, new_role, reason):
    """Tell the person whose access just moved. Best-effort by design: a
    missing notification is not worth failing a completed role change."""
    try:
        from gallery.notify import notify
        actor_name = getattr(actor, 'username', 'a Super Admin')
        notify(
            target,
            'role',
            title=f'Your role is now {new_role}',
            body=f'@{actor_name} changed your BlaqVibes role from {old_role} to '
                 f'{new_role}. Reason given: {reason}',
            url=reverse('profile_view', args=[target.username]),
        )
    except Exception:
        logger.exception('role notification failed for @%s', target.username)

    email = (target.email or '').strip()
    if not email:
        return
    try:
        from .emails import send_generic_email
        send_generic_email(
            email,
            f'Your BlaqVibes role changed to {new_role}',
            (f'Hi @{target.username},\n\n'
             f'Your BlaqVibes role was changed from {old_role} to {new_role} '
             f'by @{getattr(actor, "username", "a Super Admin")}.\n\n'
             f'Reason given: {reason}\n\n'
             f'If this was not expected, reply to this email immediately.\n'),
        )
    except Exception:
        logger.exception('role email failed for @%s', target.username)
