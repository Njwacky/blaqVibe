"""allauth hooks — mark email verified, copy GitHub handle onto Profile."""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class BlaqAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return True


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
            if handle and not profile.github:
                profile.github = str(handle)[:80]
                updates.append('github')
        if updates:
            profile.save(update_fields=updates)
        return user
