"""Paystack helpers.

Card checkout is real (initialize + signed webhook) but only when
PAYSTACK_SECRET_KEY is set. We do not fake charges or bank payouts.
"""
import hashlib
import hmac

from django.conf import settings


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
