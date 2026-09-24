"""Signal receivers: social-login sync and cache invalidation on operator writes.
"""
from allauth.socialaccount.signals import social_account_added, social_account_updated
from django.contrib.auth.signals import user_logged_in
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .adapters import sync_social_profile
from .models import FooterContact
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


@receiver(post_save, sender=FooterContact)
@receiver(post_delete, sender=FooterContact)
def _invalidate_footer_contact_cache(sender, instance, **kwargs):
    """The public footer renders from a 10-minute cache (perf:footer_contacts:v1).

    Without this, an operator's edit — via the /admin/footer-contacts/ editor
    or Django admin — goes live only when the TTL expires, so the old contact
    details serve on every page for up to ten minutes after every save.
    Signals are the one place the invalidation cannot be forgotten: they fire
    for every write path, including queryset deletes in the Django admin,
    which a save()/delete() override on the model would miss.
    """
    from gallery.performance import FOOTER_CONTACTS_CACHE_KEY
    cache.delete(FOOTER_CONTACTS_CACHE_KEY)
