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

from .models import Profile, StarEvent, Tip, WELCOME_STARS

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


def send_tip(sender, recipient, amount, message=''):
    """Move stars from sender's wallet to recipient's — zero-sum, ledgered.

    Returns the Tip row. Raises ValueError with a user-facing message on any
    invalid state (self-tip, bad amount, unverified sender, insufficient
    balance). Mirrors trade_for_download's discipline exactly:

    5 Whys:
    1. Why never create stars? The economy rule: a free action that mints
       currency is farmable. Tips move existing stars or fail.
    2. Why select_for_update on BOTH wallets? Two concurrent tips must not
       both read the same balance and overspend — locking serializes them.
    3. Why ledger rows inside the same transaction as the balance moves?
       A crash between UPDATE and INSERT leaves a balance the ledger cannot
       explain — the exact bug the ledger exists to catch.
    4. Why gate on the sender's verified email? The sender moves spendable
       currency — same rule as the buyer in trade_for_download. Unverified
       accounts are free to script; verified mailboxes are the cheapest
       real cost we can demand.
    5. Why is the Tip row created before the ledger refs? The ledger rows
       carry ref='tip:<pk>'; the anchor row must exist in the transaction.
    """
    if not sender or not getattr(sender, 'pk', None) or not recipient:
        raise ValueError('Sign in to tip.')
    if sender.pk == recipient.pk:
        raise ValueError('You cannot tip yourself.')
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise ValueError('Tip must be a whole number of stars.')
    if amount < 1:
        raise ValueError('Tip must be at least 1 star.')
    if amount > 1000:
        raise ValueError('Max 1000 stars per tip.')
    try:
        if not sender.profile.email_verified:
            raise ValueError(
                'Confirm your email before tipping — same rule as trading. '
                'Check your inbox for the verification link.'
            )
    except Profile.DoesNotExist as exc:
        raise ValueError('Account profile is missing. Refresh and try again.') from exc

    try:
        with transaction.atomic():
            try:
                sender_p = Profile.objects.select_for_update().get(user=sender)
                recipient_p = Profile.objects.select_for_update().get(user=recipient)
            except Profile.DoesNotExist as exc:
                raise ValueError('Account profile is missing. Refresh and try again.') from exc
            if sender_p.stars_balance < amount:
                raise ValueError(
                    f'You have {sender_p.stars_balance} ★ — not enough for a '
                    f'{amount}★ tip. Earn by trading your vibes.'
                )
            tip = Tip.objects.create(
                sender=sender,
                recipient=recipient,
                amount=amount,
                message=(message or '')[:200],
            )
            Profile.objects.filter(pk=sender_p.pk).update(stars_balance=F('stars_balance') - amount)
            Profile.objects.filter(pk=recipient_p.pk).update(stars_balance=F('stars_balance') + amount)
            StarEvent.objects.create(
                user=sender, delta=-amount, reason='tip_spend', ref=f'tip:{tip.pk}'
            )
            StarEvent.objects.create(
                user=recipient, delta=amount, reason='tip_earn', ref=f'tip:{tip.pk}'
            )
            return tip
    except ValueError:
        raise
    except IntegrityError:
        raise ValueError('Could not complete the tip. Try again.')
