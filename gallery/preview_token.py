"""Short-lived signed tokens for the snippet iframe.
"""
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

SALT = 'blaqvibes.snippet-preview'
MAX_AGE = 300

def issue_snippet_token(slug: str) -> str:
    return TimestampSigner(salt=SALT).sign(slug)

def snippet_token_is_valid(slug: str, token: str) -> bool:
    if not token or not slug:
        return False
    try:
        return TimestampSigner(salt=SALT).unsign(token, max_age=MAX_AGE) == slug
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return False
