"""Social-login signal receivers.

5 Whys (why a signal and not just the adapter):

1. Why is ``save_user`` not enough? It only runs when the social login
   *creates* a User. Someone who already has a BlaqVibes account and then
   signs in with GitHub goes down allauth's connect path, which never calls
   save_user — so their GitHub handle and verified email were dropped.
2. Why ``social_account_added`` specifically? It is the one event that fires
   for both an explicit "connect this provider" and the automatic connect
   allauth performs when a provider hands back an address that already
   belongs to a local account.
3. Why also ``social_account_updated``? Provider data is re-fetched on every
   login. A user who sets a GitHub handle after signing up here should get it
   picked up on their next sign-in, not never.
4. Why reuse the adapter's sync function instead of writing the fields here?
   Two copies of "which provider field maps to which Profile field" drift the
   first time a provider is added. One function, three call sites.
5. Why can these receivers not raise? They run inside the OAuth callback. An
   exception here turns a successful authentication into a 500 — the sync is
   best-effort by construction (it logs and moves on).
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
        import logging
        logging.getLogger(__name__).exception('Could not record login security event')
