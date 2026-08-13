"""Which OAuth providers have credentials. Never expose secrets to templates."""
from django.conf import settings


def configured_social_providers():
    providers = []
    mapping = (
        ('google', 'Continue with Google', getattr(settings, 'GOOGLE_CLIENT_ID', '')),
        ('github', 'Continue with GitHub', getattr(settings, 'GITHUB_CLIENT_ID', '')),
        ('facebook', 'Continue with Facebook', getattr(settings, 'FACEBOOK_CLIENT_ID', '')),
    )
    for slug, label, client_id in mapping:
        if client_id:
            providers.append({'id': slug, 'label': label})
    return providers
