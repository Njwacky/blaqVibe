"""allauth hooks — the OAuth sign-in path (Google, GitHub, Facebook).

5 Whys (why adapters instead of signal handlers):

1. Why hook allauth at all? A social sign-in creates a User without ever
   touching SignUpForm, so every rule that form enforces (reserved handles,
   public language) would simply not exist on the OAuth path.
2. Why the *account* adapter for usernames? allauth derives a username from
   the provider's login/name and validates it through
   ``clean_username`` — that one method is the only chokepoint every
   social signup, and the socialaccount signup form, both pass through.
3. Why not a post_save signal? A signal fires after the row exists; by then
   "@admin" is already a real account and the fix is a rename, not a block.
4. Why copy the provider handle onto Profile? The profile chip links to
   github.com/<handle>; asking a GitHub user to retype what we already
   received is a form nobody fills in.
5. Why grant the welcome stars here? The grant is bound to a *verified
   mailbox*, not to signup. Google/GitHub/Facebook hand us a provider-verified
   address, so that condition is met at first login — and grant_welcome_stars
   is ledger-idempotent, so the email link paying it later is a no-op.
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
