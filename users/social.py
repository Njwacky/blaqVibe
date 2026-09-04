"""Which OAuth providers are actually usable. Never expose secrets to templates.

5 Whys (why the template does not just ask allauth):

1. Why a helper at all? A button that starts a handshake we have no
   credentials for is worse than no button: allauth raises
   ``SocialApp.DoesNotExist`` and the user gets a 500 on what looked like the
   normal way in.
2. Why check the id AND the secret? An id alone builds a SocialApp entry that
   passes the "is it configured" glance and then fails at the token exchange —
   after the user has already approved on the provider's site.
3. Why read settings instead of the SocialApp DB table? Credentials live in
   env (see SOCIAL_PROVIDER_CREDENTIALS); there is no admin-managed row to
   drift out of sync with them.
4. Why return slug + label only? Templates get exactly what they render.
   A client id in a context dict ends up in a cached HTML fragment.
5. Why is the order fixed by the settings dict? Buttons that reorder between
   requests are a misclick generator.
"""
from django.conf import settings


def social_connection_context(user):
    """Settings-page data: which providers this user has linked, and whether
    unlinking is safe.

    5 Whys (why `can_disconnect` is computed here):
    1. Why can't the user always unlink? An account created through Google has
       no usable password. Unlink the only provider and nobody — not even the
       owner — can sign in again.
    2. Why not just let allauth refuse? It raises a form error deep in its own
       connections view. The settings page should not offer a button that is
       going to be rejected.
    3. Why is "has a password" the test? Password + email reset is the other
       way in. With either one intact, unlinking is recoverable.
    4. Why show linked providers even when their credentials were removed from
       env? The link is still a row on the account; hiding it would make the
       account look unlinked while the connection still exists.
    5. Why no unlink form of our own? allauth's connections view already owns
       that POST, with its own validation. We link to it.
    """
    accounts = []
    try:
        from allauth.socialaccount.models import SocialAccount
        labels = {
            slug: cfg['label'].replace('Continue with ', '')
            for slug, cfg in getattr(settings, 'SOCIAL_PROVIDER_CREDENTIALS', {}).items()
        }
        for account in SocialAccount.objects.filter(user=user).order_by('provider'):
            accounts.append({
                'provider': account.provider,
                'name': labels.get(account.provider, account.provider.title()),
                'uid': account.uid,
                'last_login': account.last_login,
            })
    except Exception:
        accounts = []
    return {
        'social_accounts': accounts,
        'can_disconnect_social': bool(user.has_usable_password() or len(accounts) > 1),
    }


def configured_social_providers():
    """[{'id': 'github', 'label': 'Continue with GitHub'}, ...] — may be empty."""
    providers = []
    for slug, cfg in getattr(settings, 'SOCIAL_PROVIDER_CREDENTIALS', {}).items():
        client_id = getattr(settings, cfg['id_setting'], '')
        secret = getattr(settings, cfg['secret_setting'], '')
        if client_id and secret:
            providers.append({'id': slug, 'label': cfg['label']})
    return providers
