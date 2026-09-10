"""Public footer contact methods — the data behind the footer's Contact column.

The footer used to carry three hard-coded fields on ``SiteSettings``: one
email, one GitHub URL and one GitHub label. That shape answers "how do people
reach you today?" and cannot answer the next question — a second support
mailbox, a WhatsApp number, an X account, a Telegram group — without a model
change, a migration, a form change and a deploy.

So the contact list is now data: one row per method, each with a kind (email,
WhatsApp, X, GitHub, …), the value an operator typed, an optional display
label, a position and a show/hide switch. The footer renders whatever rows
exist, in order. "The company has two emails and a WhatsApp number now" is an
edit on the admin page, not a release.

Security — the href is BUILT here, never stored. An operator cannot put
``javascript:`` in the public footer even by pasting it:

* URL kinds must parse as http(s) with a host, or no link is rendered at all;
* handle kinds (X, GitHub, Instagram) are rebuilt from the handle onto a base
  URL this module chooses, so a pasted foreign URL is rejected rather than
  silently re-pointed;
* phone numbers are reduced to digits before they reach ``wa.me`` or ``tel:``;
* email becomes ``mailto:`` only after Django's own email validator passes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db.models import Max

# A footer is a footer. Past a dozen methods it stops reading as "how to reach
# us" and starts reading as a sitemap, so the editor refuses more than this.
MAX_FOOTER_CONTACTS = 12

# Kinds. The keys are stored in the database, so they are stable strings — a
# label can change (X/Twitter) without touching a single row.
EMAIL = 'email'
PHONE = 'phone'
WHATSAPP = 'whatsapp'
TWITTER = 'twitter'
GITHUB = 'github'
INSTAGRAM = 'instagram'
LINKEDIN = 'linkedin'
TELEGRAM = 'telegram'
DISCORD = 'discord'
YOUTUBE = 'youtube'
TIKTOK = 'tiktok'
WEBSITE = 'website'
OTHER = 'other'

# Flavours: how a stored value turns into a link.
FLAVOUR_EMAIL = 'email'
FLAVOUR_PHONE = 'phone'
FLAVOUR_WHATSAPP = 'whatsapp'
FLAVOUR_HANDLE = 'handle'
FLAVOUR_URL = 'url'

_EMAIL_ERROR = 'Enter a valid email address, like support@blaqvibes.co.za.'
_PHONE_ERROR = 'Use digits, spaces, brackets, + or -. We add +27 to numbers that start with 0.'
_URL_ERROR = 'Use a complete http:// or https:// URL.'


@dataclass(frozen=True)
class ContactKind:
    """One selectable method in the operator editor."""

    key: str
    label: str          # what the operator picks in the dropdown
    icon: str           # emoji shown next to the public link
    flavour: str        # how the value becomes a link
    short: str = ''     # short name used to build a default display label
    placeholder: str = ''
    hint: str = ''      # one-line example shown under the value box
    base_url: str = ''  # handle kinds only
    hosts: tuple = ()   # handle kinds only: hosts we accept a pasted URL from


KINDS: dict[str, ContactKind] = {
    EMAIL: ContactKind(
        EMAIL, 'Email', '✉️', FLAVOUR_EMAIL, short='Email',
        placeholder='support@blaqvibes.co.za',
        hint='Opens the visitor’s mail app.',
    ),
    PHONE: ContactKind(
        PHONE, 'Phone', '📞', FLAVOUR_PHONE, short='Phone',
        placeholder='+27 82 555 0100',
        hint='Dials on a phone. Numbers starting with 0 get +27.',
    ),
    WHATSAPP: ContactKind(
        WHATSAPP, 'WhatsApp', '💬', FLAVOUR_WHATSAPP, short='WhatsApp',
        placeholder='082 555 0100',
        hint='Opens a chat on wa.me.',
    ),
    TWITTER: ContactKind(
        TWITTER, 'X (Twitter)', '𝕏', FLAVOUR_HANDLE, short='X',
        placeholder='@blaqvibes',
        hint='A handle or a full x.com / twitter.com profile URL.',
        base_url='https://x.com/', hosts=('x.com', 'twitter.com'),
    ),
    GITHUB: ContactKind(
        GITHUB, 'GitHub', '🐙', FLAVOUR_HANDLE, short='GitHub',
        placeholder='Njwacky',
        hint='A user or org name, or the full github.com URL.',
        base_url='https://github.com/', hosts=('github.com',),
    ),
    INSTAGRAM: ContactKind(
        INSTAGRAM, 'Instagram', '📸', FLAVOUR_HANDLE, short='Instagram',
        placeholder='@blaqvibes',
        hint='A handle or the full instagram.com profile URL.',
        base_url='https://instagram.com/', hosts=('instagram.com',),
    ),
    LINKEDIN: ContactKind(
        LINKEDIN, 'LinkedIn', '💼', FLAVOUR_URL, short='LinkedIn',
        placeholder='https://linkedin.com/company/blaqvibes',
        hint='The full page URL.',
    ),
    TELEGRAM: ContactKind(
        TELEGRAM, 'Telegram', '✈️', FLAVOUR_URL, short='Telegram',
        placeholder='https://t.me/blaqvibes',
        hint='A t.me username or group invite URL.',
    ),
    DISCORD: ContactKind(
        DISCORD, 'Discord', '🎮', FLAVOUR_URL, short='Discord',
        placeholder='https://discord.gg/blaqvibes',
        hint='A discord.gg invite.',
    ),
    YOUTUBE: ContactKind(
        YOUTUBE, 'YouTube', '▶️', FLAVOUR_URL, short='YouTube',
        placeholder='https://youtube.com/@blaqvibes',
        hint='The channel URL.',
    ),
    TIKTOK: ContactKind(
        TIKTOK, 'TikTok', '🎵', FLAVOUR_URL, short='TikTok',
        placeholder='https://tiktok.com/@blaqvibes',
        hint='The profile URL.',
    ),
    WEBSITE: ContactKind(
        WEBSITE, 'Website', '🌐', FLAVOUR_URL, short='Website',
        placeholder='https://blaqvibes.co.za',
        hint='Any http(s) page — shown as its bare domain.',
    ),
    OTHER: ContactKind(
        OTHER, 'Other link', '🔗', FLAVOUR_URL, short='Link',
        placeholder='https://status.blaqvibes.co.za',
        hint='Anything else: status page, form, docs.',
    ),
}

# Unknown kinds (a row written by a future build, or by hand) still render —
# as a plain link with a neutral icon, never as an error.
_FALLBACK_KIND = ContactKind(OTHER, 'Link', '🔗', FLAVOUR_URL, short='Link')


def contact_kind_choices():
    """Model choices. A callable, so adding a kind is not a migration."""
    return [(kind.key, kind.label) for kind in KINDS.values()]


def kind_meta(kind: str) -> ContactKind:
    return KINDS.get(kind) or _FALLBACK_KIND


def kind_label(kind: str) -> str:
    return kind_meta(kind).label


def icon_for(kind: str) -> str:
    return kind_meta(kind).icon


def kind_guide() -> dict:
    """Per-kind placeholder + hint for the editor's value box (used by JS)."""
    return {
        kind.key: {'placeholder': kind.placeholder, 'hint': kind.hint}
        for kind in KINDS.values()
    }


# --- value normalisation ----------------------------------------------------

_PHONE_SHAPE = re.compile(r'^\+?[0-9][0-9\s().-]{6,22}$')
_HANDLE_SHAPE = re.compile(r'^[A-Za-z0-9._-]{1,60}(/[A-Za-z0-9._-]{1,60})?$')


def digits_only(value: str) -> str:
    return re.sub(r'\D', '', value or '')


def normalize_value(kind: str, raw: str) -> str:
    """Validate what an operator typed and return the canonical stored value.

    Raises ``ValidationError`` with wording an operator can act on. Called by
    the editor form, by ``FooterContact.clean()`` (so the Django admin gets the
    same rule) and by ``FooterContact.save()``.
    """
    meta = kind_meta(kind)
    value = (raw or '').strip()
    if not value:
        return ''

    if meta.flavour == FLAVOUR_EMAIL:
        value = value.lower()
        EmailValidator(_EMAIL_ERROR)(value)
        return value

    if meta.flavour in (FLAVOUR_PHONE, FLAVOUR_WHATSAPP):
        return _normalize_phone(value)

    if meta.flavour == FLAVOUR_HANDLE:
        return _normalize_handle(value, meta)

    return _normalize_url(value)


def _normalize_phone(value: str) -> str:
    if not _PHONE_SHAPE.match(value):
        raise ValidationError(_PHONE_ERROR)
    digits = digits_only(value)
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('0'):
        # Durban-first product: a leading 0 is a South African local number,
        # and typing the international code is the step people forget.
        digits = '27' + digits[1:]
    if not 8 <= len(digits) <= 15:
        raise ValidationError('Enter the full number, including its country code.')
    return '+' + digits


def _normalize_handle(value: str, meta: ContactKind) -> str:
    handle = value.lstrip('@').strip()
    looks_like_url = '//' in handle or handle.lower().startswith(('http:', 'https:'))
    if looks_like_url:
        parsed = urlparse(handle if '//' in handle else 'https://' + handle)
        host = (parsed.netloc or '').lower().removeprefix('www.')
        # A pasted URL from another site is a mistake, not a redirect: turning
        # facebook.com/me into x.com/me would put the wrong link on every page.
        if host and not any(host == h or host.endswith('.' + h) for h in meta.hosts):
            raise ValidationError(
                f'That link is not {meta.label}. Paste the {meta.label} profile URL, or just the handle.'
            )
        handle = (parsed.path or '').strip('/')
    handle = handle.strip().strip('/')
    if not _HANDLE_SHAPE.match(handle):
        raise ValidationError(f'Enter a {meta.label} handle, or the full profile URL.')
    return handle


def _normalize_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValidationError(_URL_ERROR)
    return value


# --- rendering --------------------------------------------------------------

def build_href(kind: str, value: str) -> str:
    """The link for a stored value. Empty means "render nothing"."""
    meta = kind_meta(kind)
    value = (value or '').strip()
    if not value:
        return ''
    if meta.flavour == FLAVOUR_EMAIL:
        return f'mailto:{value}'
    if meta.flavour == FLAVOUR_PHONE:
        return f'tel:{value}'
    if meta.flavour == FLAVOUR_WHATSAPP:
        return f'https://wa.me/{digits_only(value)}'
    if meta.flavour == FLAVOUR_HANDLE:
        return f'{meta.base_url}{value}'
    # URL flavour: only ever echo a scheme we chose. A row written by hand (or
    # by a build that skipped validation) must not be able to ship a
    # javascript: or data: link into every page on the site.
    parsed = urlparse(value)
    if parsed.scheme in ('http', 'https') and parsed.netloc:
        return value
    return ''


def display_label(kind: str, value: str, label: str = '') -> str:
    """What visitors read: the operator's label, or a sensible default."""
    label = (label or '').strip()
    if label:
        return label
    meta = kind_meta(kind)
    value = (value or '').strip()
    if not value:
        return meta.short or meta.label
    if meta.flavour in (FLAVOUR_EMAIL, FLAVOUR_PHONE):
        return value
    if meta.flavour == FLAVOUR_WHATSAPP:
        return f'{meta.short} {value}'
    if meta.flavour == FLAVOUR_HANDLE:
        return f'{meta.short} @{value.split("/")[0]}'
    return _short_url(value)


def _short_url(value: str) -> str:
    """`https://t.me/blaqvibes` → `t.me/blaqvibes` — a footer, not a URL bar."""
    parsed = urlparse(value)
    host = (parsed.netloc or '').lower().removeprefix('www.')
    if not host:
        return value
    path = (parsed.path or '').strip('/')
    if not path:
        return host
    if len(path) > 28:
        path = path[:28] + '…'
    return f'{host}/{path}'


def contact_as_dict(contact) -> dict:
    """A cacheable, template-ready row (no model instance in the cache)."""
    href = build_href(contact.kind, contact.value)
    return {
        'kind': contact.kind,
        'icon': icon_for(contact.kind),
        'label': display_label(contact.kind, contact.value, contact.label),
        'href': href,
        'external': href.startswith('http'),
        'value': contact.value,
    }


# --- cached read + invalidation ---------------------------------------------

def public_footer_contacts() -> list[dict]:
    """The rows the public footer renders: active ones, lowest position first.

    Deliberately NOT cached. Every page view renders the footer, so caching
    looks tempting — but the default cache is per-process (LocMemCache) and
    production runs three gunicorn workers, so a cached list would keep
    serving the old contacts on the workers that did not handle the edit. An
    operator saving "we moved to this number" needs that to be true now, on
    every worker; the list is a dozen rows of one small indexed table, so the
    read is cheaper than the inconsistency it would buy.
    """
    from .models import FooterContact

    return [
        contact_as_dict(contact)
        for contact in FooterContact.objects.filter(is_active=True)
    ]


def next_positions(count: int) -> list[int]:
    """Positions for the editor's blank rows — always after what exists."""
    from .models import FooterContact

    highest = FooterContact.objects.aggregate(highest=Max('position'))['highest'] or 0
    return [highest + 10 * (step + 1) for step in range(max(count, 0))]
