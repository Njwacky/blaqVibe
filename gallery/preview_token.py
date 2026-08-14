"""Short-lived signed tokens for the snippet iframe.

5 Whys:
1. Why not trust Sec-Fetch-Dest alone? It is a client header. Missing on old
   browsers, and a top-level document is NOT sandboxed (sandbox is an iframe
   attribute).
2. Why not trust Referer path alone? `https://evil.example/app/<slug>/preview`
   has the same path. A click from that page would pass a path-only check.
3. Why a TimestampSigner? Only the server can mint a token bound to the slug,
   and it dies after a few minutes if leaked in a log or Referer.
4. Why still reject dest=document even with a valid token? Opening the iframe
   URL in a new tab must never run user JS as a first-party page.
5. Why also send CSP sandbox on the snippet response? Defense if an old
   browser has no Sec-Fetch-Dest and a stolen token is opened top-level.
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
