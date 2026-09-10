"""Server-side AnyPost integration.

The provider URL and request path are deployment configuration. No credential
or provider response is returned to the browser.
"""

import os

import requests


class AnyPostError(Exception):
    """An expected AnyPost configuration or provider failure."""


def _setting(name, default=''):
    try:
        from django.conf import settings
        value = getattr(settings, name, default)
    except Exception:
        value = os.getenv(name, default)
    return (value or default).strip()


def share_project(*, title, text, url):
    base_url = _setting('ANYPOST_BASE_URL')
    endpoint = _setting('ANYPOST_ENDPOINT')
    api_key = _setting('ANYPOST_API_KEY')
    if not base_url or not endpoint or not api_key:
        raise AnyPostError('AnyPost sharing is not configured.')
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint

    try:
        timeout = max(1, int(_setting('ANYPOST_TIMEOUT_SECONDS', '15')))
    except ValueError:
        timeout = 15
    try:
        response = requests.post(
            base_url.rstrip('/') + endpoint,
            headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
            json={'title': title, 'text': text, 'url': url},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AnyPostError('AnyPost is unavailable.') from exc
    if response.status_code >= 400:
        raise AnyPostError('AnyPost rejected the share request.')
    return True
