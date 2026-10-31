"""Visitor log — every human page view, registered or not.

THE QUESTION THIS ANSWERS
Somebody shares a BlaqVibes link. Did anyone open it? Before this module the
only trace of a visit was `VibeView`, and that row needs a signed-in account —
so the honest answer for an anonymous visitor was "we have no idea". The
operator wants to see the room, not just the people who signed the guest book.

WHAT IS RECORDED
One row per rendered HTML page (`VisitorHit`): path, when, the account if
there is one, IP, browser/OS/device guessed from the user-agent, the external
referrer, and two flags — `is_bot` and `is_internal`.

WHAT IS NOT
- Static files, media, API/JSON responses, redirects, error pages, `/admin/`
  (the honeypot), `/ops/` and health checks. A visit is a *rendered page*, not
  a cache revalidation or a prefetch.
- Anything for a request whose `Accept` header does not include `text/html`.
  That single check removes the overwhelming majority of asset and fetch noise
  without a path allowlist that would rot.
- Anything at all when `BLAQVIBES_TRACK_VISITORS=0`.

PRIVACY
This is first-party, server-side, and there is no tracking cookie and no
JavaScript. Visitors are de-duplicated with `visitor_key = HMAC(secret, ip+ua)`
so "unique visitors" can be counted without joining on a cookie. The raw IP is
stored because the operator asked for it; `ip_hash` lets the same lookup work
if the raw address is dropped by a retention sweep. Nothing here is exposed
publicly: the only reader is the `admin_required` dashboard at
`/admin/visitors/`.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

logger = logging.getLogger(__name__)

# Paths that are never a "visit". Prefixes, checked against request.path.
SKIP_PREFIXES = (
    '/static/',
    '/media/',
    '/admin/',            # honeypot + Django admin (both under protected paths)
    '/blaq-admin-secure/',
    '/api/',
    '/ops/',
    '/accounts/social/',  # OAuth round-trips, not reading
    '/healthz',
    '/readyz',
)

SKIP_PATHS = frozenset({
    '/robots.txt',
    '/sw.js',
    '/sitemap.xml',
    '/favicon.ico',
    '/manifest.webmanifest',
})

ASSET_EXTENSIONS = (
    '.css', '.js', '.mjs', '.map', '.json', '.xml', '.txt', '.csv',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.avif',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.mp3', '.mp4', '.webm', '.zip', '.pdf',
)

# Bots are recorded (a scraper's visit is still information) but excluded from
# the headline numbers. Matched case-insensitively against the whole UA.
BOT_PATTERN = re.compile(
    r'(?:bot\b|bot/|crawl|spider|slurp|bingpreview|facebookexternalhit|embedly|'
    r'headless|phantomjs|selenium|python-requests|python-urllib|httpx|'
    r'curl/|wget/|go-http-client|okhttp|axios/|node-fetch|libwww|scrapy|'
    r'monitor|pingdom|uptime|statuscake|lighthouse|pagespeed|semrush|ahrefs|'
    r'gptbot|chatgpt-user|claudebot|perplexity|ccbot|amazonbot|'
    r'whatsapp/|telegrambot|discordbot|slackbot|linkedinbot|twitterbot)',
    re.IGNORECASE,
)

# Order matters: Edge/Opera/Samsung all claim Chrome, and Chrome claims Safari.
_BROWSERS = (
    ('Edg/', 'Edge'),
    ('EdgiOS', 'Edge'),
    ('OPR/', 'Opera'),
    ('SamsungBrowser', 'Samsung Internet'),
    ('Firefox/', 'Firefox'),
    ('FxiOS', 'Firefox'),
    ('CriOS', 'Chrome'),
    ('Chrome/', 'Chrome'),
    ('Safari/', 'Safari'),
)

_OPERATING_SYSTEMS = (
    ('Windows', 'Windows'),
    ('Android', 'Android'),
    ('iPhone', 'iOS'),
    ('iPad', 'iPadOS'),
    ('iPod', 'iOS'),
    ('Mac OS X', 'macOS'),
    ('Macintosh', 'macOS'),
    ('CrOS', 'ChromeOS'),
    ('Linux', 'Linux'),
)


# --------------------------------------------------------------------------
# Small pure helpers (unit-tested; no DB, no request side effects)
# --------------------------------------------------------------------------
def _digest(value: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256,
    ).hexdigest()


def ip_hash(ip: str) -> str:
    """Stable per-install hash of an address — never a reversible encoding."""
    return _digest(f'visitor-ip:{ip}') if ip else ''


def visitor_key(ip: str, user_agent: str) -> str:
    """A cookie-less identity for "the same person on the same device".

    Deliberately coarse: NAT means two people can share a key, and a UA bump
    means one person can look like two. Counting uniques this way is honest
    about being an estimate, which a tracking cookie would not be.
    """
    return _digest(f'visitor:{ip or "unknown"}|{(user_agent or "").lower()}')


def parse_user_agent(user_agent: str) -> dict:
    """{'device': 'Phone|Tablet|Desktop|Bot', 'browser': str, 'os': str, 'is_bot': bool}

    Heuristic by design — no dependency, no network call. Labels are for a
    human reading a table, never used for a decision.
    """
    ua = (user_agent or '').strip()
    is_bot = (not ua) or bool(BOT_PATTERN.search(ua))

    browser = 'Unknown'
    for token, label in _BROWSERS:
        if token in ua:
            browser = label
            break

    operating_system = 'Unknown'
    for token, label in _OPERATING_SYSTEMS:
        if token in ua:
            operating_system = label
            break

    if is_bot:
        device = 'Bot'
    elif 'iPad' in ua or 'Tablet' in ua or ('Android' in ua and 'Mobile' not in ua):
        device = 'Tablet'
    elif 'Mobi' in ua or 'iPhone' in ua or 'iPod' in ua or 'Android' in ua:
        device = 'Phone'
    else:
        device = 'Desktop'

    return {
        'device': device,
        'browser': browser[:40],
        'os': operating_system[:40],
        'is_bot': is_bot,
    }


def client_ip(request) -> str | None:
    """The visitor's address, honouring X-Forwarded-For only for trusted proxies.

    Delegates to `users.security.client_ip` — the login risk checks already
    decided which proxies may speak for a client, and two answers to that
    question would be one too many.
    """
    raw = ''
    try:
        from users.security import client_ip as _trusted_client_ip
        raw = (_trusted_client_ip(request) or '').strip()
    except Exception:
        raw = (request.META.get('REMOTE_ADDR') or '').strip()
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def format_duration(seconds) -> str:
    """Human label for a span: '38s', '1m 42s', '1h 04m'. Empty for None."""
    if seconds is None:
        return ''
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m {seconds % 60:02d}s'
    return f'{seconds // 3600}h {(seconds % 3600) // 60:02d}m'


def session_idle_window() -> timedelta:
    """How long a gap still counts as the same visit.

    Two page views 4 minutes apart are one person reading; two 3 hours apart
    are two visits. 30 minutes is the industry default and is env-tunable so a
    site with long-form content can say 90 without a code change.
    """
    return timedelta(minutes=int(getattr(settings, 'VISITOR_SESSION_IDLE_MINUTES', 30)))


def forwarded_ip(request) -> str | None:
    """First X-Forwarded-For hop, as an UNVERIFIED display hint.

    `client_ip` is the address the platform stands behind: it honours the
    header only for proxies the operator named. Most deployments (Render,
    nginx, the Arena preview) are behind one, and without naming it every row
    would show the proxy's address. So the header is also kept separately, in
    a field that no decision reads and the dashboard labels "unverified".
    """
    raw = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def is_internal_user(user) -> bool:
    """Team accounts: their visits are logged but kept out of visitor numbers."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    try:
        return bool(user.profile.is_admin())
    except Exception:
        return False


def is_html_request(request) -> bool:
    accept = request.META.get('HTTP_ACCEPT', '') or ''
    return 'text/html' in accept


def should_track(request, response) -> bool:
    """True only for a rendered HTML page a human could have read."""
    if not getattr(settings, 'BLAQVIBES_TRACK_VISITORS', True):
        return False
    if request.method != 'GET':
        return False
    if getattr(response, 'status_code', 0) != 200:
        # Redirects, 304 revalidations and error pages are not visits; logging
        # them would double-count a link that hops through `/` and pad the
        # numbers with 404s from scanners.
        return False
    path = request.path or '/'
    if path in SKIP_PATHS:
        return False
    if any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    if path.lower().endswith(ASSET_EXTENSIONS):
        return False
    if not is_html_request(request):
        return False
    if (request.META.get('HTTP_X_REQUESTED_WITH') or '').lower() == 'xmlhttprequest':
        return False
    # A ZIP download or a file preview can answer 200 to a browser navigation
    # and even claim text/html in `Accept`; it is not a page anybody read.
    try:
        content_type = response.get('Content-Type', '') or ''
        disposition = response.get('Content-Disposition', '') or ''
    except Exception:
        content_type, disposition = '', ''
    if content_type and 'text/html' not in content_type.lower():
        return False
    if 'attachment' in disposition.lower():
        return False
    return True


def referrer_parts(request) -> tuple[str, str]:
    """(full referrer, hostname) — blanked for on-site navigation.

    An internal link is not "where they came from": showing `/?page=2` as a
    source buries the one useful signal (an external share) in noise.
    """
    raw = (request.META.get('HTTP_REFERER') or '').strip()[:500]
    if not raw:
        return '', ''
    try:
        host = (urlparse(raw).hostname or '').lower()
    except ValueError:
        host = ''
    own_host = ''
    try:
        own_host = (request.get_host() or '').split(':')[0].lower()
    except Exception:
        own_host = ''
    if host and host == own_host:
        return '', ''
    return raw, host[:255]


# --------------------------------------------------------------------------
# Writer — the single place a VisitorHit row is created
# --------------------------------------------------------------------------
def record_visit(request, response):
    """Log one page view. Returns the row, or None when it was not a visit.

    Called from `gallery.middleware.VisitorTrackingMiddleware` after the
    response is built, wrapped so that a logging failure can never turn a
    working page into a 500.
    """
    if not should_track(request, response):
        return None

    from .models import VisitorHit

    user = getattr(request, 'user', None)
    signed_in = bool(user is not None and getattr(user, 'is_authenticated', False))
    user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:400]
    parsed = parse_user_agent(user_agent)
    ip = client_ip(request)
    hinted_ip = forwarded_ip(request)
    if hinted_ip == ip:
        hinted_ip = None
    referrer, referrer_host = referrer_parts(request)

    session_key = ''
    try:
        session_key = (request.session.session_key or '')[:64]
    except Exception:
        session_key = ''

    key = visitor_key(ip or '', user_agent)
    now = timezone.now()

    # One indexed lookup per page view, reused three ways: "is this visitor
    # new?", "is this the same visit?" and "how long did they spend on the
    # previous page?". A visit is a run of hits whose gaps are all under
    # VISITOR_SESSION_IDLE_MINUTES; the gap that ends a run is also the dwell
    # time recorded against the page they were on.
    previous = None
    try:
        previous = (
            VisitorHit.objects.filter(visitor_key=key)
            .order_by('-created_at')
            .first()
        )
    except Exception:
        previous = None

    first_visit = previous is None
    continuing = bool(previous) and (now - previous.created_at) <= session_idle_window()
    if continuing:
        session_id = previous.session_id or uuid.uuid4().hex
        session_started_at = previous.session_started_at or previous.created_at
    else:
        session_id = uuid.uuid4().hex
        session_started_at = now

    try:
        hit = VisitorHit.objects.create(
            path=request.path[:500],
            method=request.method[:8],
            status_code=response.status_code,
            user=user if signed_in else None,
            ip=ip,
            forwarded_ip=hinted_ip,
            ip_hash=ip_hash(ip) if ip else '',
            visitor_key=key,
            session_key=session_key,
            user_agent=user_agent,
            device=parsed['device'],
            browser=parsed['browser'],
            os=parsed['os'],
            referrer=referrer,
            referrer_host=referrer_host,
            is_bot=parsed['is_bot'],
            is_internal=is_internal_user(user),
            is_first_visit=first_visit,
            session_id=session_id,
            session_started_at=session_started_at,
        )
    except Exception:
        # Logging must never break the page it is logging.
        logger.debug('visitor log write failed', exc_info=True)
        return None

    if continuing:
        # Close the previous page now that we know when they left it. Only the
        # first measurement wins (a concurrent request must not overwrite it
        # with a shorter one), and it is never negative.
        try:
            VisitorHit.objects.filter(
                pk=previous.pk, dwell_seconds__isnull=True,
            ).update(
                dwell_seconds=max(0, int((now - previous.created_at).total_seconds())),
            )
        except Exception:
            logger.debug('dwell time write failed', exc_info=True)

    return hit


# --------------------------------------------------------------------------
# Reader helpers
# --------------------------------------------------------------------------
def daily_series(queryset, days=14):
    """[(date, page_views, unique_visitors)] zero-filled over the window.

    Unique visitors per day is `COUNT(DISTINCT visitor_key)` inside that day —
    so the daily uniques will not sum to the window's unique count, and they
    are not supposed to.
    """
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        queryset.filter(created_at__date__gte=start)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(views=Count('id'), uniques=Count('visitor_key', distinct=True))
        .order_by('day')
    )
    by_day = {r['day']: (r['views'], r['uniques']) for r in rows if r['day']}
    return [
        (start + timedelta(days=i), *by_day.get(start + timedelta(days=i), (0, 0)))
        for i in range(days)
    ]
