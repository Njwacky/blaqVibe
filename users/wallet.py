"""Star wallet operations — every balance move writes a StarEvent row.
"""
import logging

from django.db import IntegrityError, transaction
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
