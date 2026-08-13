"""Paystack helpers.

Card checkout is real (initialize + signed webhook) but only when
PAYSTACK_SECRET_KEY is set. We do not fake charges or bank payouts.
"""
from django.conf import settings


def paystack_secret() -> str:
    return (getattr(settings, 'PAYSTACK_SECRET_KEY', '') or '').strip()


def paystack_enabled() -> bool:
    return bool(paystack_secret())
