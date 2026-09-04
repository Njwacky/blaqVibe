"""Project + account lifecycle — deletes that never destroy money records.

5 Whys:
1. Why does delete need its own module? "Delete" used to be a one-line
   project.delete() in two views — and it cascaded paid Trade/Sale rows
   and buyers' ZIPs with it.
2. Why can't the views just check inline? Account deletion, vibe deletion
   and moderation deletion all need the identical rule; three inline
   copies drift.
3. Why soft-delete only when money exists? A vibe nobody paid for has no
   record worth keeping — hard delete honours "I want this gone".
4. Why does the DB also enforce it (PROTECT)? Because the next developer
   will call .delete() somewhere new; the constraint turns that bug into
   a loud ProtectedError instead of silent data loss.
5. Why a sentinel owner for released vibes instead of NULL? AppProject.owner
   is non-null everywhere (templates, ranks, notify). One well-known ghost
   user keeps every code path working while clearly reading "deleted
   account" in the UI.
"""
import logging

from django.contrib.auth.models import User
from django.db import transaction
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
        with transaction.atomic():
            locked = AppProject.objects.select_for_update().get(pk=project.pk)
            return _soft(locked)


def release_account_projects(user):
    """Called BEFORE user.delete(): keep sold vibes alive under the ghost.

    - Paid vibes → owner becomes the ghost user, status becomes 'removed'
      (buyers keep downloading; the public page is gone).
    - Unpaid vibes → left alone; the user cascade hard-deletes them, which
      is exactly the erasure the account owner asked for.
    """
    ghost = get_ghost_user()
    moved = 0
    for project in AppProject.objects.filter(owner=user):
        if has_paid_records(project):
            project.owner = ghost
            project.status = 'removed'
            project.is_featured = False
            project.save(update_fields=['owner', 'status', 'is_featured'])
            moved += 1
    if moved:
        logger.info('released %s paid vibes from @%s to ghost', moved, user.username)
    return moved
