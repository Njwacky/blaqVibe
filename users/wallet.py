"""Star wallet operations — every balance move writes a StarEvent row.

5 Whys (why this module exists):
1. Why one module? stars_balance was mutated from four different files;
   any new call site could forget the ledger row.
2. Why must the ledger row and the UPDATE share a transaction?
   A crash between them leaves a balance the ledger cannot explain —
   the exact support ticket the ledger exists to answer.
3. Why is the welcome grant here and not in the signup view?
   Signup is free and scriptable. The grant is bound to email
   verification, which can happen in three places (verify link, social
   login, admin). One idempotent function keeps them consistent.
4. Why idempotent via the ledger, not a boolean on Profile?
   The ledger row IS the fact "this user was paid the grant".
   A second boolean would be duplicated state that can drift.
5. Why select_for_update on Profile? Two concurrent verify clicks (or a
   social login racing the email link) must not pay the grant twice.
"""
import logging

from django.db import transaction
from django.db.models import F, Sum

from .models import Profile, StarEvent, WELCOME_STARS

logger = logging.getLogger(__name__)


def grant_welcome_stars(user) -> bool:
    """Pay the one-time welcome grant. Returns True only when it was paid now.

    Safe to call every time email_verified flips on — the ledger row makes
    it idempotent.
    """
    if not user or not getattr(user, 'pk', None):
        return False
    try:
        with transaction.atomic():
            profile = (
                Profile.objects.select_for_update().filter(user=user).first()
            )
            if profile is None:
                return False
            if StarEvent.objects.filter(user=user, reason='welcome').exists():
                return False
            Profile.objects.filter(pk=profile.pk).update(
                stars_balance=F('stars_balance') + WELCOME_STARS
            )
            StarEvent.objects.create(
                user=user,
                delta=WELCOME_STARS,
                reason='welcome',
                ref='email-verify',
            )
            return True
    except Exception:
        # Never break login/verify over the grant; the next verify retries it.
        logger.exception('welcome grant failed for user=%s', getattr(user, 'pk', None))
        return False


def ledger_balance(user) -> int:
    """Sum of all ledger rows — must equal Profile.stars_balance."""
    return StarEvent.objects.filter(user=user).aggregate(total=Sum('delta'))['total'] or 0


def wallet_reconciles(user) -> bool:
    """True when the integer balance matches the ledger. Support tool."""
    try:
        return user.profile.stars_balance == ledger_balance(user)
    except Profile.DoesNotExist:
        return False
