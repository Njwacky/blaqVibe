"""Customer checkout via Paystack.

BlaqVibes charges buyers for published projects. It does not initiate creator
cash-outs or bank transfers. Creator compensation/payback is deliberately not
part of this payment boundary.
"""
import hashlib
import hmac
import json
import logging
import secrets
from datetime import timedelta
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

INTENT_TTL = timedelta(minutes=30)
PAYSTACK_CURRENCY = 'ZAR'

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

def verify_paystack_transaction(reference: str) -> dict:
    if not reference:
        raise PaymentError('missing reference')
    response = requests.get(
        f'https://api.paystack.co/transaction/verify/{quote(reference, safe="")}',
        headers={'Authorization': f'Bearer {paystack_secret()}'},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get('status'):
        raise PaymentError('Paystack did not verify this reference.')
    return payload.get('data') or {}

def _authorization_headers():
    return {
        'Authorization': f'Bearer {paystack_secret()}',
        'Content-Type': 'application/json',
    }

def initiate_payout_transfer(*args, **kwargs):
    """Disabled permanently: this application does not pay creators out."""
    raise PaymentError('Creator cash-outs are disabled. Paystack is for customer purchases only.')

def create_checkout(user, project):
    """Charge a buyer for a published project and return its Paystack URL."""
    from .models import PaymentIntent, Sale

    if not paystack_enabled():
        raise PaymentError(
            "Card payments aren't configured. Set PAYSTACK_SECRET_KEY, or trade stars to download."
        )
    if not user or not getattr(user, 'is_authenticated', False):
        raise PaymentError('Sign in to buy this project.')
    if not (getattr(user, 'email', None) or '').strip():
        raise PaymentError('Add an email to your account before paying by card.')
    if project.owner_id == user.id:
        raise PaymentError('You already own this project.')
    if getattr(project, 'status', None) != 'published' or not getattr(project, 'zip_file', None):
        raise PaymentError('This project is not for sale.')
    amount_zar = int(project.price_zar or 0)
    if amount_zar <= 0:
        raise PaymentError('This project is not for sale.')

    now = timezone.now()
    with transaction.atomic():
        if Sale.objects.select_for_update().filter(buyer=user, project=project).exists():
            raise PaymentError('already_unlocked')

        pending = (
            PaymentIntent.objects.select_for_update()
            .filter(buyer=user, project=project, status='pending')
            .order_by('-created_at')
            .first()
        )
        if pending and pending.authorization_url and pending.amount_zar == amount_zar and pending.expires_at and pending.expires_at > now:
            return pending.authorization_url
        if pending:
            pending.status = 'failed'
            pending.save(update_fields=['status'])

        reference = f'blaq-{project.id}-{user.id}-{secrets.token_hex(8)}'
        intent = PaymentIntent.objects.create(
            reference=reference,
            buyer=user,
            project=project,
            amount_zar=amount_zar,
            amount_kobo=amount_zar * 100,
            currency=PAYSTACK_CURRENCY,
            status='pending',
            expires_at=now + INTENT_TTL,
        )

    payload = {
        'email': user.email.strip(),
        'amount': intent.amount_kobo,
        'currency': PAYSTACK_CURRENCY,
        'reference': intent.reference,
        'callback_url': f'{settings.SITE_URL}{project.get_absolute_url()}',
        'metadata': {'project_id': project.id, 'buyer_id': user.id, 'intent_id': intent.id},
    }
    try:
        response = requests.post(
            'https://api.paystack.co/transaction/initialize',
            json=payload,
            headers=_authorization_headers(),
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
    intent.authorization_url = url
    intent.save(update_fields=['authorization_url'])
    return url

def fulfill_signed_webhook(body: bytes, signature: str) -> tuple[int, str]:
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
    return _fulfill_intent(data.get('reference') or '', int(data.get('amount') or 0), (data.get('currency') or PAYSTACK_CURRENCY).upper())

def _fulfill_intent(reference: str, paid_kobo: int, currency: str) -> tuple[int, str]:
    from .models import PaymentIntent, Sale
    from .notify import notify

    if not reference:
        return 400, 'missing reference'
    try:
        with transaction.atomic():
            try:
                intent = PaymentIntent.objects.select_for_update().select_related('buyer', 'project', 'project__owner').get(reference=reference)
            except PaymentIntent.DoesNotExist:
                return 400, 'unknown reference'
            if currency != (intent.currency or PAYSTACK_CURRENCY).upper():
                return 400, 'currency mismatch'
            if paid_kobo != intent.amount_kobo:
                return 400, 'amount mismatch'
            if intent.status == 'paid':
                return 200, 'already fulfilled'

            try:
                verified = verify_paystack_transaction(reference)
            except PaymentError as exc:
                return 400, str(exc)
            except Exception:
                logger.exception('Paystack verify outage ref=%s', reference)
                return 500, 'verify unavailable'

            if (verified.get('status') or '').lower() != 'success':
                return 400, 'not successful'
            if int(verified.get('amount') or 0) != intent.amount_kobo:
                return 400, 'verify amount mismatch'
            if (verified.get('currency') or PAYSTACK_CURRENCY).upper() != (intent.currency or PAYSTACK_CURRENCY).upper():
                return 400, 'verify currency mismatch'
            if (verified.get('reference') or reference) != reference:
                return 400, 'verify reference mismatch'

            sale, created = Sale.objects.get_or_create(
                buyer=intent.buyer,
                project=intent.project,
                defaults={'seller': intent.project.owner, 'amount_zar': intent.amount_zar, 'paystack_ref': intent.reference},
            )
            if not created and not sale.paystack_ref:
                sale.paystack_ref = intent.reference
                sale.save(update_fields=['paystack_ref'])
            intent.status = 'paid'
            intent.paid_at = timezone.now()
            intent.save(update_fields=['status', 'paid_at'])
            if created:
                notify(intent.project.owner, 'sale', f'{intent.buyer.username} bought {intent.project.title}', f'R{intent.amount_zar}', intent.project.get_absolute_url())
                notify(intent.buyer, 'sale', f'You unlocked {intent.project.title}', url=intent.project.get_absolute_url())
            return 200, 'ok'
    except Exception:
        logger.exception('fulfill intent failed ref=%s', reference)
        return 500, 'fulfill failed'
