"""Creator cash-outs — stars held at request, ZAR paid by a money admin.
This module is the ONLY writer of Payout rows and their two ledger reasons.
Views render and validate input; the rules live here so every call site
gets them for free (same discipline as users/wallet.py and
gallery/economy.py).
"""
import logging
import re

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from gallery.notify import notify

from .models import (
    MAX_PAYOUT_STARS,
    MIN_PAYOUT_STARS,
    STARS_PER_ZAR,
    AdminLog,
    Payout,
    Profile,
    StarEvent,
)

logger = logging.getLogger(__name__)

_ACCOUNT_RE = re.compile(r'^[0-9]{4,20}$')

class PayoutError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)

def payout_rate_label() -> str:
    return f'{STARS_PER_ZAR} ★ = R1 (minimum {MIN_PAYOUT_STARS} ★, in multiples of {STARS_PER_ZAR})'

def request_payout(user, amount_stars, bank_name, account_number, holder_name):
    """Hold stars and queue a cash-out. Returns the Payout row.

    Raises PayoutError with a user-facing message on any invalid state —
    never raises after partially mutating the wallet (all writes share
    one transaction).
    """
    if not user or not getattr(user, 'is_authenticated', False):
        raise PayoutError('Sign in to cash out.')
    try:
        amount = int(str(amount_stars).strip())
    except (TypeError, ValueError):
        raise PayoutError('Amount must be a whole number of stars.')
    if amount < MIN_PAYOUT_STARS:
        raise PayoutError(
            f'Minimum cash-out is {MIN_PAYOUT_STARS} ★ (R{MIN_PAYOUT_STARS // STARS_PER_ZAR}).'
        )
    if amount > MAX_PAYOUT_STARS:
        raise PayoutError(
            f'Maximum cash-out is {MAX_PAYOUT_STARS} ★ (R{MAX_PAYOUT_STARS // STARS_PER_ZAR}) per request.'
        )
    if amount % STARS_PER_ZAR:
        raise PayoutError(f'Cash out in multiples of {STARS_PER_ZAR} ★ so the ZAR amount is whole.')

    bank = (bank_name or '').strip()
    holder = (holder_name or '').strip()
    account = (account_number or '').strip()
    if len(bank) < 2:
        raise PayoutError('Which bank should be paid? e.g. Capitec, FNB, Standard Bank.')
    if len(holder) < 2:
        raise PayoutError('Account holder name is required — the bank rejects mismatched names.')
    if not _ACCOUNT_RE.match(account):
        raise PayoutError('Account number must be 4–20 digits.')

    try:
        if not user.profile.email_verified:
            raise PayoutError(
                'Confirm your email before cashing out — same rule as trading and tipping.'
            )
    except Profile.DoesNotExist as exc:
        raise PayoutError('Account profile is missing. Refresh and try again.') from exc

    try:
        with transaction.atomic():
            profile = Profile.objects.select_for_update().get(user=user)
            if Payout.objects.select_for_update().filter(user=user, status='requested').exists():
                raise PayoutError('You already have a cash-out in review. Wait for it to be paid or rejected.')
            if profile.stars_balance < amount:
                raise PayoutError(
                    f'You have {profile.stars_balance} ★ — not enough for a {amount}★ cash-out.'
                )
            payout = Payout(
                user=user,
                amount_stars=amount,
                amount_zar=amount // STARS_PER_ZAR,
                status='requested',
                bank_name=bank[:100],
                account_number=account,
                holder_name=holder[:80],
            )
            payout.save()
            Profile.objects.filter(pk=profile.pk).update(
                stars_balance=F('stars_balance') - amount
            )
            StarEvent.objects.create(
                user=user, delta=-amount, reason='payout_hold', ref=f'payout:{payout.pk}'
            )
    except PayoutError:
        raise
    except Exception as exc:
        logger.exception('payout request failed user=%s', getattr(user, 'pk', None))
        raise PayoutError('Could not queue the cash-out. Nothing was debited — try again.') from exc

    notify(
        user,
        'payout',
        f'Cash-out requested: R{payout.amount_zar}',
        f'{amount} ★ is now held. A money admin reviews payouts — you will be notified when it is paid or rejected.',
    )
    return payout

def decide_payout(admin_user, payout_id, action, note=''):
    """Admin decision on a requested payout. Returns the updated row.

    action='pay'    → mark paid (admin confirmed real money moved).
    action='reject' → refund held stars to the wallet and mark rejected.
    Any provider transfer reference is recorded verbatim on the row.

    Raises PayoutError on unknown rows, finished rows, or bad input.
    """
    action = (action or '').strip().lower()
    if action not in ('pay', 'reject'):
        raise PayoutError('Unknown payout action.')
    note = (note or '').strip()[:200]

    try:
        with transaction.atomic():
            payout = (
                Payout.objects.select_for_update()
                .select_related('user')
                .get(pk=payout_id)
            )
            if payout.status != 'requested':
                raise PayoutError(f'This payout is already {payout.status}.')
            if not payout.user:
                raise PayoutError('The requesting account was deleted — resolve manually in the ledger.')

            payout.status = 'paid' if action == 'pay' else 'rejected'
            payout.reviewed_by = admin_user if getattr(admin_user, 'is_authenticated', False) else None
            payout.admin_note = note
            payout.decided_at = timezone.now()

            if action == 'reject':
                profile = Profile.objects.select_for_update().get(user=payout.user)
                Profile.objects.filter(pk=profile.pk).update(
                    stars_balance=F('stars_balance') + payout.amount_stars
                )
                StarEvent.objects.create(
                    user=payout.user,
                    delta=payout.amount_stars,
                    reason='payout_refund',
                    ref=f'payout:{payout.pk}',
                )
            payout.save(update_fields=[
                'status', 'reviewed_by', 'admin_note', 'decided_at',
            ])
    except PayoutError:
        raise
    except Exception as exc:
        logger.exception('payout decide failed id=%s action=%s', payout_id, action)
        raise PayoutError('Could not record the decision. Try again.') from exc

    try:
        AdminLog.objects.create(
            actor=admin_user,
            action='payout_' + action,
            target=f'payout #{payout.pk}: {payout.amount_stars}★ → R{payout.amount_zar} for '
                   f'@{payout.user.username}' + (f' — {note}' if note else ''),
        )
    except Exception:
        logger.exception('payout AdminLog failed id=%s', payout.pk)

    if action == 'reject':
        notify(
            payout.user,
            'payout',
            f'Cash-out rejected — {payout.amount_stars} ★ returned',
            note or 'The stars are back in your wallet. Fix the details and try again.',
        )
    else:
        notify(
            payout.user,
            'payout',
            f'Cash-out paid: R{payout.amount_zar}',
            note or f'Paid to {payout.bank_name} {payout.account_masked}.',
        )
    return payout

def record_transfer_reference(payout_id, provider_ref):
    """Store a Paystack transfer code on a still-requested payout.

    Initiating a transfer is NOT payment — it can fail or await OTP for
    days. The reference is a receipt for the admin; only decide_payout
    (human confirmation) flips the row to 'paid'.
    """
    ref = (provider_ref or '').strip()[:100]
    if not ref:
        raise PayoutError('Empty transfer reference.')
    updated = Payout.objects.filter(pk=payout_id, status='requested').update(
        provider_ref=ref
    )
    if not updated:
        raise PayoutError('Only a still-requested payout can take a transfer reference.')
    return ref
