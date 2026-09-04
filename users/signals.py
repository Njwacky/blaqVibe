"""Social-login signal receivers.
"""
from allauth.socialaccount.signals import social_account_added, social_account_updated
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .adapters import sync_social_profile
from .security import record_login

@receiver(social_account_added)
@receiver(social_account_updated)
def _sync_profile_from_social(sender, request, sociallogin, **kwargs):
    user = getattr(sociallogin, 'user', None)
    if user is not None and getattr(user, 'pk', None):
        sync_social_profile(user, sociallogin)

@receiver(user_logged_in)
def _record_interactive_login(sender, request, user, **kwargs):
    """Cover password and OAuth login paths with the same risk signal."""
    try:
        record_login(request, user)
    except Exception:
        # Login must remain available when mail/audit storage is unavailable.
        import logging
        logging.getLogger(__name__).exception('Could not record login security event')
