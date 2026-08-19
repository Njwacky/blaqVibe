"""allauth hooks — mark email verified, copy GitHub handle onto Profile,
and keep provider handles from ever becoming public usernames.

5 Whys — why gate social usernames here?
1. GitHub/Google signups never touch SignUpForm: with
   SOCIALACCOUNT_AUTO_SIGNUP=True allauth copies the provider handle
   straight onto the User row. A provider handle like "fuckyou" became
   @fuckyou on every card, untouched by every form gate.
2. Why block auto-signup instead of silently renaming? The honest path
   is to make the person pick their own clean handle in the social
   signup form (templates/socialaccount/signup.html) — they see the
   reason, they choose the name, nothing is rewritten behind their back.
3. Why keep a save_user backstop anyway? Defense in depth: if any path
   still reaches save with a blocked username (a future provider, a
   manual SocialLogin), the account is force-renamed to user_<pk> and
   TOLD via an in-app notification. Silent leaks die; the person learns.
4. Why gate clean_username on the account adapter? The social signup
   form's username field validates through that hook — one place covers
   the form path for every provider.
5. Why not copy a dirty GitHub handle onto Profile.github? The profile
   page would display it publicly. We leave the field blank — hiding,
   not rewriting.
"""
from django.core.exceptions import ValidationError

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from gallery.profanity import PUBLIC_LANGUAGE_ERROR, contains_profanity

USERNAME_LANGUAGE_ERROR = (
    'That username cannot be used on BlaqVibes. ' + PUBLIC_LANGUAGE_ERROR
)


class BlaqAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return True

    def clean_username(self, username, shallow=False):
        """Allauth's username hook — now also the public-language gate.

        Runs for every username allauth accepts (social signup form,
        email signup via allauth views). Our own SignUpForm keeps its
        own check too; both call the same word list.
        """
        username = super().clean_username(username, shallow=shallow)
        if contains_profanity(username):
            raise ValidationError(USERNAME_LANGUAGE_ERROR)
        return username


class BlaqSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def is_auto_signup_allowed(self, request, sociallogin):
        """Refuse auto-signup when the provider handle is abusive.

        Returning False sends the person to the social signup form with
        a username field, so THEY pick a clean handle instead of us
        silently inventing one. The proposed username is already set on
        sociallogin.user by the time allauth asks this question.
        """
        if not super().is_auto_signup_allowed(request, sociallogin):
            return False
        user = getattr(sociallogin, 'user', None)
        username = getattr(user, 'username', '') or ''
        if contains_profanity(username):
            return False
        return True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        email = (data.get('email') or '').strip().lower()
        if email and not user.email:
            user.email = email
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)

        # Last line of defense: auto-signup was blocked for dirty handles
        # and the signup form rejects them, so this should never fire.
        # If it ever does, the account is force-renamed and told why —
        # never silently left as @slur.
        renamed_to = force_clean_username(user)

        extra = sociallogin.account.extra_data or {}
        profile = getattr(user, 'profile', None)
        if profile is None:
            return user
        updates = []
        if user.email and not profile.email_verified:
            profile.email_verified = True
            updates.append('email_verified')
        if sociallogin.account.provider == 'github':
            handle = extra.get('login') or extra.get('username') or ''
            # Never display a blocked handle on the profile — leave the
            # field empty instead of copying the words across.
            if handle and not profile.github and not contains_profanity(str(handle)):
                profile.github = str(handle)[:80]
                updates.append('github')
        if updates:
            profile.save(update_fields=updates)
        # A provider-verified email is a verified mailbox — pay the same
        # one-time welcome grant the email link pays. Idempotent via ledger.
        if profile.email_verified:
            from .wallet import grant_welcome_stars
            grant_welcome_stars(user)

        if renamed_to:
            from gallery.notify import notify
            notify(
                user,
                'moderation',
                'Your BlaqVibes username was changed',
                (
                    f'Your previous username came from your '
                    f'{sociallogin.account.provider} account and broke our '
                    f'public-language rules, so it is now @{renamed_to}. '
                    f'Your account, vibes and stars are untouched.'
                ),
                f'/u/{renamed_to}/',
            )
        return user


def force_clean_username(user) -> str | None:
    """Rename `user` to a neutral handle when their username is blocked.

    Returns the new username, or None when nothing changed. The new
    handle is user_<pk> (plus a suffix on the rare collision) — neutral,
    unique, and obviously a placeholder the person will want to replace.
    """
    username = user.username or ''
    if not contains_profanity(username):
        return None
    from django.contrib.auth.models import User

    candidate = f'user_{user.pk}'
    suffix = 0
    while User.objects.filter(username=candidate).exclude(pk=user.pk).exists():
        suffix += 1
        candidate = f'user_{user.pk}_{suffix}'
    user.username = candidate
    user.save(update_fields=['username'])
    return candidate
