"""allauth hooks — the OAuth sign-in path (Google, GitHub, Facebook).
"""
import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

class BlaqAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return True

    def clean_username(self, username, shallow=False):
        """Same handle rules as SignUpForm, on the social path too.

        allauth calls this for every candidate username it derives from a
        provider profile. Raising ValidationError makes allauth try the next
        candidate (``octo`` -> ``octo1`` -> ...), so a blocked name degrades
        into a suffixed one instead of a 500.
        """
        username = super().clean_username(username, shallow=shallow)
        # Imported lazily: users.rename imports models, and adapters are
        # loaded from settings before the app registry is ready.
        from .rename import RESERVED_USERNAMES
        from gallery.profanity import public_text_is_clean

        if username.lower() in RESERVED_USERNAMES:
            raise ValidationError('That username is reserved.')
        if not public_text_is_clean(username):
            raise ValidationError('That username is not allowed.')
        return username

class BlaqSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        email = (data.get('email') or '').strip().lower()
        if email and not user.email:
            user.email = email
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        sync_social_profile(user, sociallogin)
        return user

def sync_social_profile(user, sociallogin):
    """Mirror what the provider told us onto the Profile.

    Called from two places, because a social account reaches a User two
    different ways: ``save_user`` (brand new signup) and the
    ``social_account_added`` signal (an existing account connecting a
    provider, where allauth never calls save_user at all).

    Never raises: a cosmetic field or a star grant must not be able to turn a
    successful authentication into a 500 on the OAuth callback.
    """
    try:
        account = sociallogin.account
        extra = account.extra_data or {}
        profile = getattr(user, 'profile', None)
        if profile is None:
            return
        updates = []
        # An address that came back verified from the provider is a verified
        # mailbox. sociallogin.email_addresses carries the provider's own
        # verified flag; user.email on its own proves nothing.
        email = (user.email or '').lower()
        verified = any(
            addr.verified and (addr.email or '').lower() == email
            for addr in (sociallogin.email_addresses or [])
        )
        if verified and not profile.email_verified:
            profile.email_verified = True
            updates.append('email_verified')
        if account.provider == 'github':
            handle = extra.get('login') or extra.get('username') or ''
            if handle and not profile.github:
                profile.github = str(handle)[:80]
                updates.append('github')
        if updates:
            profile.save(update_fields=updates)
        if profile.email_verified:
            from .wallet import grant_welcome_stars
            grant_welcome_stars(user)
    except Exception:
        logger.exception(
            'social profile sync failed for user=%s', getattr(user, 'pk', None)
        )
