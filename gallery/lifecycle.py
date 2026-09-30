"""Project + account lifecycle — deletes that never destroy money records.
"""
import logging

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.db.models.deletion import ProtectedError

from .models import AppProject, Sale, Trade

logger = logging.getLogger(__name__)

GHOST_USERNAME = 'ghost'

def has_paid_records(project) -> bool:
    """True when someone paid stars or ZAR for this project."""
    return (
        Trade.objects.filter(project=project).exists()
        or Sale.objects.filter(project=project).exists()
    )

def get_ghost_user():
    """The well-known owner for vibes whose creator deleted their account.

    is_active=False and unusable password: nobody can ever log in as it.
    """
    ghost, created = User.objects.get_or_create(
        username=GHOST_USERNAME,
        defaults={'email': '', 'is_active': False},
    )
    if created:
        ghost.set_unusable_password()
        ghost.save(update_fields=['password'])
    return ghost

def park_project(project) -> str:
    """Soft-delete a vibe WITHOUT ever destroying it. Returns 'removed'.

    Why this is separate from remove_project(): remove_project is the owner's
    own delete, so it is allowed to be hard (nothing was ever paid → the bytes
    go). Attention cases (gallery/attention.py) park a build the platform
    chose to drop, and a machine decision must be reversible: the loser goes
    off the public site, buyers keep their receipts, and the owner can restore
    it or press FINAL DELETE themselves. Erasing on a timer is what the
    final-delete step is for, and it uses remove_project — money-aware, like
    every other delete in the codebase.
    """
    with transaction.atomic():
        locked = AppProject.objects.select_for_update().get(pk=project.pk)
        locked.status = 'removed'
        locked.is_featured = False
        locked.save(update_fields=['status', 'is_featured'])
    return 'removed'

def restore_project(project, status: str) -> str:
    """Undo a park: put the vibe back at the status it had before.

    `status` comes from AttentionCandidate.snapshot, which recorded it when the
    case opened, so restoring a build that was pending scan does not silently
    publish it. Anything unrecognized falls back to 'pending' — visible to the
    owner, invisible to the feed, which is the safe direction to be wrong in.
    """
    allowed = {'pending', 'published', 'quarantined'}
    target = status if status in allowed else 'pending'
    with transaction.atomic():
        locked = AppProject.objects.select_for_update().get(pk=project.pk)
        locked.status = target
        locked.save(update_fields=['status'])
    return target

def remove_project(project) -> str:
    """Delete a vibe without destroying purchases.

    Returns 'deleted' (hard, nothing was ever paid) or 'removed'
    (soft — buyers keep their download, everyone else loses the page).
    """
    def _soft(locked):
        locked.status = 'removed'
        locked.is_featured = False
        locked.save(update_fields=['status', 'is_featured'])
        return 'removed'

    try:
        with transaction.atomic():
            locked = AppProject.objects.select_for_update().get(pk=project.pk)
            if has_paid_records(locked):
                return _soft(locked)
            locked.delete()
            return 'deleted'
    except ProtectedError:
        # A Sale/Trade landed between the check and the delete (e.g. a
        # Paystack webhook). The PROTECT constraint caught it — money now
        # exists, so soft-delete instead.
        with transaction.atomic():
            locked = AppProject.objects.select_for_update().get(pk=project.pk)
            return _soft(locked)

def paid_projects_of(user_ids):
    """Projects owned by any of these accounts that somebody paid for.

    ONE query for the whole set. The per-project `has_paid_records` helper
    above costs two queries per vibe, which is fine for one owner deleting
    their own account and ruinous when staff clear a hundred of them.
    """
    return AppProject.objects.filter(owner_id__in=list(user_ids)).filter(
        Exists(Trade.objects.filter(project=OuterRef('pk')))
        | Exists(Sale.objects.filter(project=OuterRef('pk')))
    )

def release_accounts_projects(users):
    """Called BEFORE the users are deleted: keep sold vibes alive under the ghost.

    - Paid vibes → owner becomes the ghost user, status becomes 'removed'
      (buyers keep downloading; the public page is gone).
    - Unpaid vibes → left alone; the user cascade hard-deletes them, which
      is exactly the erasure the account owner asked for.

    The batch form exists for staff clean-up (users/account_cleanup.py). The
    moves still go through `project.save()`, one sold vibe at a time, exactly
    as the single-account version always did: AppProject.save() is the
    model's one write path, and a bulk UPDATE would quietly skip whatever
    that path does now or later. Sold vibes are the rare case, so the loop is
    short; what the batch removes is the per-owner, per-vibe lookups around it.
    """
    users = list(users)
    if not users:
        return 0
    ghost = get_ghost_user()
    moved = 0
    for project in paid_projects_of(u.pk for u in users):
        project.owner = ghost
        project.status = 'removed'
        project.is_featured = False
        project.save(update_fields=['owner', 'status', 'is_featured'])
        moved += 1
    if moved:
        logger.info('released %s paid vibes from %s account(s) to ghost', moved, len(users))
    return moved

def release_account_projects(user):
    """Single-account form: the owner deleting their own account.

    Delegates, so there is exactly one implementation of "what happens to a
    sold vibe when its creator goes".
    """
    return release_accounts_projects([user])
