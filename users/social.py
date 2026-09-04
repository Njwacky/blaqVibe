"""Which OAuth providers are actually usable. Never expose secrets to templates.
"""
from django.conf import settings

def social_connection_context(user):
    """Settings-page data: which providers this user has linked, and whether
        unlinking is safe.
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
        # Safe to unlink one when a password exists, or when another link
        # would remain behind.
        'can_disconnect_social': bool(user.has_usable_password() or len(accounts) > 1),
    }

def configured_social_providers():
    """[{'id': 'github', 'label': 'Continue with GitHub'}, ...] — may be empty."""
    providers = []
    for slug, cfg in getattr(settings, 'SOCIAL_PROVIDER_CREDENTIALS', {}).items():
        # Read the credentials through `settings` (not a value captured at
        # import) so overriding GITHUB_CLIENT_ID at runtime flips the button.
        client_id = getattr(settings, cfg['id_setting'], '')
        secret = getattr(settings, cfg['secret_setting'], '')
        if client_id and secret:
            providers.append({'id': slug, 'label': cfg['label']})
    return providers
