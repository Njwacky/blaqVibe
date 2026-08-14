"""Paystack helpers.

Card checkout is real (initialize + signed webhook) but only when
PAYSTACK_SECRET_KEY is set. We do not fake charges or bank payouts.

Checkout creates a PaymentIntent that freezes amount + buyer + project.
The webhook fulfills that row — never the live price_zar.
"""
import hashlib
import hmac
import json
import logging
import secrets

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def paystack_secret() -> str:
    return (getattr(settings, 'PAYSTACK_SECRET_KEY', '') or '').strip()


def paystack_enabled() -> bool:
    return bool(paystack_secret())


def verify_paystack_signature(body: bytes, sig: str) -> bool:
    secret = paystack_secret()
    if not secret or not sig:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, sig)


def create_checkout(user, project):
    """Freeze price, create PaymentIntent, initialize Paystack.

    Returns the authorization URL. Raises PaymentError on any failure
    before a charge exists.
    """
    from .models import PaymentIntent, Sale

    if not paystack_enabled():
        raise PaymentError(
            "Card payments aren't configured. Set PAYSTACK_SECRET_KEY, or trade stars to download."
        )
    if not user or not getattr(user, 'is_authenticated', False):
        raise PaymentError('Sign in to buy this vibe.')
    if project.owner_id == user.id:
        raise PaymentError('You already own this vibe.')
    amount_zar = int(project.price_zar or 0)
    if amount_zar <= 0:
        raise PaymentError('This vibe is not for sale.')
    if Sale.objects.filter(buyer=user, project=project).exists():
        raise PaymentError('already_unlocked')

    reference = f'blaq-{project.id}-{user.id}-{secrets.token_hex(6)}'
    intent = PaymentIntent.objects.create(
        reference=reference,
        buyer=user,
        project=project,
        amount_zar=amount_zar,
        amount_kobo=amount_zar * 100,
        status='pending',
    )
    headers = {
        'Authorization': f'Bearer {paystack_secret()}',
        'Content-Type': 'application/json',
    }
    payload = {
        'email': user.email or f'{user.username}@blaqvibes.co.za',
        'amount': intent.amount_kobo,
        'reference': intent.reference,
        'callback_url': f'{settings.SITE_URL}{project.get_absolute_url()}',
        'metadata': {
            'project_id': project.id,
            'buyer_id': user.id,
            'intent_id': intent.id,
        },
    }
    try:
        response = requests.post(
            'https://api.paystack.co/transaction/initialize',
            json=payload,
            headers=headers,
            timeout=10,
        )
        data = response.json()
    except Exception as exc:
        logger.warning('Paystack init fail: %s', exc)
        intent.status = 'failed'
        intent.save(update_fields=['status'])
        raise PaymentError('Could not start checkout. No charge was made.') from exc

    url = (data.get('data') or {}).get('authorization_url') if data.get('status') else None
    if not url:
        logger.warning('Paystack init rejected: %s', data)
        intent.status = 'failed'
        intent.save(update_fields=['status'])
        raise PaymentError('Could not start checkout. No charge was made.')
    return url


def fulfill_signed_webhook(body: bytes, signature: str) -> tuple[int, str]:
    """Verify signature and fulfill a PaymentIntent.

    Returns (http_status, message). 2xx only when the event is handled
    or safely ignored so Paystack stops retrying.
    """
    if not paystack_enabled():
        return 503, 'webhook not configured'
    if not verify_paystack_signature(body, signature):
        return 400, 'invalid signature'
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return 400, 'invalid json'
    if payload.get('event') != 'charge.success':
        return 200, 'ignored'
    data = payload.get('data') or {}
    return _fulfill_intent(data.get('reference') or '', int(data.get('amount') or 0))


def _fulfill_intent(reference: str, paid_kobo: int) -> tuple[int, str]:
    from .models import PaymentIntent, Sale
    from .notify import notify

    if not reference:
        return 400, 'missing reference'
    try:
        with transaction.atomic():
            try:
                intent = (
                    PaymentIntent.objects.select_for_update()
                    .select_related('buyer', 'project', 'project__owner')
                    .get(reference=reference)
                )
            except PaymentIntent.DoesNotExist:
                return 400, 'unknown reference'
            if paid_kobo != intent.amount_kobo:
                logger.warning(
                    'Paystack amount mismatch ref=%s paid=%s expected=%s',
                    reference, paid_kobo, intent.amount_kobo,
                )
                return 400, 'amount mismatch'
            if intent.status == 'paid':
                return 200, 'already fulfilled'
            sale, created = Sale.objects.get_or_create(
                buyer=intent.buyer,
                project=intent.project,
                defaults={
                    'seller': intent.project.owner,
                    'amount_zar': intent.amount_zar,
                    'paystack_ref': intent.reference,
                },
            )
            intent.status = 'paid'
            intent.paid_at = timezone.now()
            intent.save(update_fields=['status', 'paid_at'])
            if created:
                notify(
                    intent.project.owner,
                    'sale',
                    f'{intent.buyer.username} bought {intent.project.title}',
                    f'R{intent.amount_zar}',
                    intent.project.get_absolute_url(),
                )
                notify(
                    intent.buyer,
                    'sale',
                    f'You unlocked {intent.project.title}',
                    url=intent.project.get_absolute_url(),
                )
            return 200, 'ok'
    except Exception:
        logger.exception('fulfill intent failed ref=%s', reference)
        return 500, 'fulfill failed'
