"""Quarantine clean-up — find held accounts, see the blast radius, delete in bulk.

THE PROBLEM THIS SOLVES
/moderation/appeals/ lists every live hold, unbounded, one card each, with
lift/extend/expire and NO way to delete. Staff clearing a pile of accounts
that nobody is going to appeal had two options: Django admin, or leave them.
Django admin has no "quarantined" filter, its bulk delete refuses any account
that owns a sold vibe (PROTECT), and it skips everything this app promises
around a delete — the action is audited, the person is told.

THE 5 WHYS
1. Why search + select + review, and not "delete all quarantined"? Because a
   list is only safe to act on if the operator can SEE what they are about to
   destroy. The page narrows the set (search, reason, ready/blocked), the
   review step shows the blast radius of exactly that set, and the delete
   acts on exactly the ids that were reviewed — never on a filter that can
   quietly match something new between the click and the commit.
2. Why refuse some accounts instead of letting staff override? Every refusal
   protects a person or a record that cannot be put back: someone with an
   appeal waiting (nobody has answered them yet), someone whose 72-hour
   appeal window is still open (the hold may be a false positive they have not
   had time to contest), a staff account (changing power is a role change,
   with its own audit and confirmation), an account with a purchase in flight
   (Paystack would take the money and find no buyer). A refusal is reported
   per account with its reason, and it clears by itself or by the fix named.
3. Why delete in chunks through one queryset delete, not `user.delete()` in a
   loop? Measured on this schema (SQLite query counts; Postgres runs the same
   statements): ONE account costs ~97 queries, because the ORM probes every
   table that points at a user. A chunk of 25 costs 88 — 3.5 per account — and
   100 accounts cost 349 in four chunks, against ~9,700 one at a time. Cost
   follows the number of TABLES, not of accounts. On a remote Postgres that is
   the difference between a couple of seconds and a blown 30-second request.
   If a chunk fails (a payment landed mid-delete and PROTECT caught it) it is
   retried one account at a time, so one bad account never costs the batch.
4. Why typed confirmation AND a reason? The typed phrase ("DELETE 12") carries
   the count, so a click on the wrong page, or a stale tab, cannot confirm a
   number the operator never saw. The reason is the why that AdminLog keeps
   when the account itself no longer exists to be asked.
5. Why hard delete, not a soft "deactivated" state? The account being gone IS
   the goal here (usernames freed, spam removed, data erased on request), and
   the quarantine that precedes it is already the reversible stage: the
   person was held, told, given 72+ hours and an appeal form. What must NOT be
   destroyed — sold vibes, Trade/Sale receipts — is kept by the same
   mechanism the self-service "Delete account" uses (gallery.lifecycle).

WRITER RULE
This module is the only writer for bulk account removal. The views call
`review()` and `delete_quarantined_accounts()`; nothing else deletes a
quarantined account, so the guards, the audit rows and the notices cannot
drift apart. Refusals are RETURNED, never raised: a refused account is a
normal outcome of a staff page, and the `role_required` decorator turns any
exception that escapes a view into a 403 page.

PRIVACY
Email addresses are personal data. The page shows none. Searching by email is
available to super admins only — the same rule as /admin/roles/ — and a plain
admin searching `kwame@mail.com` simply finds nothing.

UNTRUSTED INPUT
Ids, the search text and the reason all arrive from a browser that may have
been scripted or tampered with, and every one is cleaned before it is used:
ids are plain digits that fit the database's integer column, free text is
printable and one line, and usernames are data — never markup, never log
syntax. See "Hostile input" below; tests/HostileInputTests pin each rule.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import (
    BooleanField, Case, Count, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When,
)
from django.db.models.functions import Coalesce, Lower
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format

from blaqvibes.envparse import normalize_rate
from gallery.lifecycle import GHOST_USERNAME, release_accounts_projects
from gallery.models import AppProject, PaymentIntent, Sale, Trade

from .models import AdminLog, QuarantineAppeal, RuleViolation, UserQuarantine
from .roles import ROLE_ORDER
from .user_search import normalize

try:  # allauth holds several addresses per person; User.email holds one
    from allauth.account.models import EmailAddress
except Exception:  # pragma: no cover - defensive
    EmailAddress = None

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Tunables. Everything an operator might reasonably change is a SETTING
# (blaqvibes/settings.py, documented in .env.example); the DEFAULT_* values
# below are what applies when a setting is absent or unusable. What is left as
# a plain constant is fixed on purpose:
#   CHUNK_SIZE    a measured trade-off between query count and lock time
#   PAGE_SIZES    the choices the page offers
#   REASON_*      tied to AdminLog.target, a 200-character column
#   STAFF_ROLES   derived from users/roles.ROLE_ORDER (see below), not a second list to keep in step
# ----------------------------------------------------------------------
DEFAULT_MIN_HOLD_HOURS = 72   # how long a person has had to appeal before the account can go
DEFAULT_MAX_BATCH = 100       # accounts per confirmed delete
DEFAULT_PAYMENT_HOLD_HOURS = 24        # a pending checkout younger than this may still be paid
DEFAULT_NOTICE_BUDGET_SECONDS = 6      # notices are best-effort; they never hold the request hostage
DEFAULT_REVIEW_RATE = '120/h'
DEFAULT_DELETE_RATE = '30/h'
CHUNK_SIZE = 25               # accounts per database transaction
PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50
REASON_MIN = 5                # a "why" that carries information (same floor as role changes)
REASON_MAX = 100              # AdminLog.target is 200 chars and also carries the facts

# Every role ranked above an ordinary user is staff and is never deleted by this tool.
# Derived from the role ladder, so a role added there later is protected by default.
STAFF_ROLES = tuple(role for role, rank in ROLE_ORDER.items() if rank > ROLE_ORDER['user'])
REASON_LABELS = dict(RuleViolation.KINDS)

SHOWS = (
    ('all', 'All held accounts'),
    ('ready', 'Ready to delete'),
    ('blocked', 'Blocked (see why)'),
)
SORTS = (
    ('held', 'Held longest first'),
    ('recent', 'Newest hold first'),
    ('ends', 'Lifts soonest first'),
    ('strikes', 'Most strikes first'),
    ('vibes', 'Most vibes first'),
    ('name', 'Username A–Z'),
)


def _int_setting(name, default, *, minimum, maximum):
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def min_hold_hours() -> int:
    """0 switches the appeal-window rule off (tests, or a team that wants it off)."""
    return _int_setting('ACCOUNT_CLEANUP_MIN_HOLD_HOURS', DEFAULT_MIN_HOLD_HOURS, minimum=0, maximum=24 * 30)


def max_batch() -> int:
    return _int_setting('ACCOUNT_CLEANUP_MAX_BATCH', DEFAULT_MAX_BATCH, minimum=1, maximum=500)


def payment_hold_hours() -> int:
    """How long a pending checkout keeps its buyer and its seller undeletable.
    At least 1: 0 would quietly switch off the guard that protects customers' money."""
    return _int_setting(
        'ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS', DEFAULT_PAYMENT_HOLD_HOURS, minimum=1, maximum=24 * 30,
    )


def notice_budget_seconds() -> int:
    """Total seconds the notice emails may take. At least 1, at most 20: gunicorn's
    default worker timeout is 30 s and one email can take BREVO_TIMEOUT on top."""
    return _int_setting(
        'ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS', DEFAULT_NOTICE_BUDGET_SECONDS, minimum=1, maximum=20,
    )


def _rate_setting(name, default) -> str:
    return normalize_rate(getattr(settings, name, default)) or default


def review_rate() -> str:
    return _rate_setting('ACCOUNT_CLEANUP_REVIEW_RATE', DEFAULT_REVIEW_RATE)


def delete_rate() -> str:
    return _rate_setting('ACCOUNT_CLEANUP_DELETE_RATE', DEFAULT_DELETE_RATE)


# django-ratelimit calls a callable `rate` as rate(group, request) on EVERY
# request, so a setting that changed (an override, a test) applies at once and a
# typo in one falls back to the default instead of failing inside a request.
def review_rate_for(group, request) -> str:
    return review_rate()


def delete_rate_for(group, request) -> str:
    return delete_rate()


def confirm_phrase(count: int) -> str:
    """What the operator types. Carries the number they reviewed."""
    return f'DELETE {count}'


# ----------------------------------------------------------------------
# Who may use this. The views carry @admin_required; so does the engine, so a
# future endpoint cannot forget it (same defence-in-depth as users/roles.py).
# ----------------------------------------------------------------------
def _profile_says(user, predicate) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    try:
        return bool(getattr(user.profile, predicate)())
    except Exception:
        return False


def is_admin(user) -> bool:
    return _profile_says(user, 'is_admin')


def can_search_email(user) -> bool:
    """Email is personal data: super admins only (see users/user_search.py)."""
    return _profile_says(user, 'is_superadmin')


# ----------------------------------------------------------------------
# The held set — one annotated queryset that the page, the review and the
# delete all read, so "what the page showed" and "what the guard checks" are
# the same SQL and cannot drift.
# ----------------------------------------------------------------------
def held_accounts(*, actor, now=None):
    """Every account with a LIVE hold, plus everything needed to judge it.

    `filter(Exists(...))` goes first on purpose: it narrows a table of tens of
    thousands of users to the handful that are held before any per-row
    subquery is evaluated.
    """
    now = now or timezone.now()
    live = UserQuarantine.objects.filter(
        user=OuterRef('pk'), status='active', ends_at__gt=now,
    ).order_by('-ends_at', '-pk')
    hours = min_hold_hours()

    in_flight = PaymentIntent.objects.filter(
        status='pending', created_at__gte=now - timedelta(hours=payment_hold_hours()),
    ).filter(Q(buyer=OuterRef('pk')) | Q(project__owner=OuterRef('pk')))

    owned = AppProject.objects.filter(owner=OuterRef('pk')).order_by().values('owner')
    sold = (
        AppProject.objects.filter(owner=OuterRef('pk'))
        .filter(
            Exists(Trade.objects.filter(project=OuterRef('pk')))
            | Exists(Sale.objects.filter(project=OuterRef('pk')))
        )
        .order_by().values('owner')
    )
    # Other builders' remixes that LOSE their "remixed from" link: only remixes
    # of vibes that get deleted. A sold vibe survives under the ghost account,
    # so a remix of it keeps its parent.
    remixed = (
        AppProject.objects.filter(forked_from__owner=OuterRef('pk'))
        .exclude(owner=OuterRef('pk'))
        .exclude(forked_from__trades__isnull=False)
        .exclude(forked_from__sales__isnull=False)
        .order_by().values('forked_from__owner')
    )

    def _count(subquery):
        return Coalesce(
            Subquery(subquery.annotate(n=Count('pk')).values('n'), output_field=IntegerField()),
            Value(0),
        )

    def _flag(condition):
        return Case(When(condition, then=Value(True)), default=Value(False), output_field=BooleanField())

    qs = (
        User.objects
        .filter(Exists(live))
        .select_related('profile')
        .annotate(
            hold_id=Subquery(live.values('pk')[:1]),
            hold_reason=Subquery(live.values('reason')[:1]),
            hold_detail=Subquery(live.values('detail')[:1]),
            hold_source=Subquery(live.values('source')[:1]),
            hold_started=Subquery(live.values('started_at')[:1]),
            hold_ends=Subquery(live.values('ends_at')[:1]),
            hold_strikes=Subquery(live.values('strike_count')[:1]),
            has_open_appeal=Exists(QuarantineAppeal.objects.filter(user=OuterRef('pk'), status='open')),
            payment_in_flight=Exists(in_flight),
            vibes_total=_count(owned),
            vibes_sold=_count(sold),
            remixed_by_others=_count(remixed),
            stars=Coalesce(F('profile__stars_balance'), Value(0), output_field=IntegerField()),
        )
    )
    actor_pk = getattr(actor, 'pk', None)
    return qs.annotate(
        is_staffer=_flag(Q(profile__role__in=STAFF_ROLES) | Q(is_staff=True) | Q(is_superuser=True)),
        is_system=_flag(Q(username=GHOST_USERNAME)),
        is_self=_flag(Q(pk=actor_pk)) if actor_pk else Value(False, output_field=BooleanField()),
        too_fresh=(
            _flag(Q(hold_started__gt=now - timedelta(hours=hours)))
            if hours else Value(False, output_field=BooleanField())
        ),
    )


_READY = dict(
    is_staffer=False, is_system=False, is_self=False,
    has_open_appeal=False, payment_in_flight=False, too_fresh=False,
)


def only_ready(qs):
    return qs.filter(**_READY)


def only_blocked(qs):
    return qs.exclude(**_READY)


def blockers_for(user) -> list[tuple[str, str]]:
    """Why this account cannot be deleted right now: [(code, sentence)].

    Reads the annotations from `held_accounts`, so it costs no query. Empty
    list = ready. Order is "most permanent first".
    """
    out = []
    if user.is_self:
        out.append(('self', 'This is your own account — use Settings → Delete account.'))
    if user.is_staffer:
        out.append(('staff', 'Staff account — change the role first (Manage roles).'))
    if user.is_system:
        out.append(('system', 'System account — it keeps the vibes people paid for.'))
    if user.has_open_appeal:
        out.append(('appeal', 'Appeal waiting — decide it on the appeals page first.'))
    if user.too_fresh:
        opens = user.hold_started + timedelta(hours=min_hold_hours())
        out.append(('fresh', f'Appeal window open until {_when(opens)}.'))
    if user.payment_in_flight:
        out.append((
            'payment',
            f'A checkout from the last {payment_hold_hours()} h may still be paid — try again later.',
        ))
    return out


def _when(moment) -> str:
    try:
        return date_format(timezone.localtime(moment), 'j M, H:i')
    except Exception:
        return moment.isoformat(timespec='minutes')


# ----------------------------------------------------------------------
# Searching, filtering, paging.
# ----------------------------------------------------------------------
@dataclass
class Filters:
    query: str = ''
    reason: str = ''
    show: str = 'all'
    sort: str = 'held'
    per: int = DEFAULT_PAGE_SIZE
    page: int = 1

    def params(self, **override) -> dict:
        """Non-default values only, so URLs stay short and readable."""
        merged = {**self.__dict__, **override}
        defaults = Filters()
        out = {}
        for key, value in merged.items():
            if key == 'query':
                if value:
                    out['q'] = value
            elif value not in ('', None) and value != getattr(defaults, key):
                out[key] = value
        return out


def _positive_int(raw, default):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def parse_filters(params) -> Filters:
    """Anything malformed falls back to the default — a hand-edited URL is never a 500."""
    reason = (params.get('reason') or '').strip()
    show = (params.get('show') or 'all').strip()
    sort = (params.get('sort') or 'held').strip()
    per = _positive_int(params.get('per'), DEFAULT_PAGE_SIZE)
    return Filters(
        query=normalize(_printable(params.get('q'))).strip(),  # normalize('@ x') keeps the space after the @
        reason=reason if reason in REASON_LABELS else '',
        show=show if show in dict(SHOWS) else 'all',
        sort=sort if sort in dict(SORTS) else 'held',
        per=per if per in PAGE_SIZES else DEFAULT_PAGE_SIZE,
        page=_positive_int(params.get('page'), 1),
    )


def apply_search(qs, query, *, include_email=False):
    """Every word must match (AND), each against the username — and, for super
    admins only, the email addresses. The held set is small, so `icontains`
    is cheap here; the ranked search in user_search.py exists for the whole
    user table, which this page never scans."""
    for term in (t.lstrip('@') for t in normalize(query).lower().split()):
        if not term:
            continue
        match = Q(username__icontains=term)
        if include_email:
            match |= Q(email__icontains=term)
            if EmailAddress is not None:
                match |= Exists(EmailAddress.objects.filter(user=OuterRef('pk'), email__icontains=term))
        qs = qs.filter(match)
    return qs


def _ordering(sort):
    return {
        'held': ('hold_started', 'pk'),
        'recent': ('-hold_started', 'pk'),
        'ends': ('hold_ends', 'pk'),
        'strikes': ('-hold_strikes', 'hold_started', 'pk'),
        'vibes': ('-vibes_total', 'hold_started', 'pk'),
        'name': (Lower('username'), 'pk'),
    }[sort]


@dataclass
class Listing:
    filters: Filters
    page: object
    rows: list
    held_total: int
    ready_total: int
    reason_counts: list          # [(slug, label, n)] for every kind, over the whole held set
    min_hold_hours: int
    max_batch: int

    @property
    def blocked_total(self) -> int:
        return self.held_total - self.ready_total

    @property
    def matched(self) -> int:
        return self.page.paginator.count

    @property
    def selectable(self) -> int:
        """Rows on this page the operator can tick (the rest say why not)."""
        return sum(1 for row in self.rows if not row.blockers)


def listing(actor, filters: Filters, *, include_email=False, now=None) -> Listing:
    """One page of the held set: bounded work however many accounts exist."""
    now = now or timezone.now()
    base = held_accounts(actor=actor, now=now)

    totals = base.aggregate(held=Count('pk'), ready=Count('pk', filter=Q(**_READY)))
    per_reason = dict(
        base.order_by().values_list('hold_reason').annotate(n=Count('pk')),
    )
    # Every kind, zeros included: a dropdown whose options appear and vanish
    # with the data makes the operator's saved filter silently mean "nothing".
    reason_counts = [(slug, label, per_reason.get(slug, 0)) for slug, label in RuleViolation.KINDS]

    qs = apply_search(base, filters.query, include_email=include_email)
    if filters.reason:
        qs = qs.filter(hold_reason=filters.reason)
    if filters.show == 'ready':
        qs = only_ready(qs)
    elif filters.show == 'blocked':
        qs = only_blocked(qs)
    qs = qs.order_by(*_ordering(filters.sort))

    page = Paginator(qs, filters.per).get_page(filters.page)
    rows = list(page.object_list)
    for row in rows:
        row.blockers = blockers_for(row)
        row.reason_label = REASON_LABELS.get(row.hold_reason, row.hold_reason)
        row.days_left = max(0, -(-int((row.hold_ends - now).total_seconds()) // 86400))
    return Listing(
        filters=filters, page=page, rows=rows,
        held_total=totals['held'] or 0, ready_total=totals['ready'] or 0,
        reason_counts=reason_counts, min_hold_hours=min_hold_hours(), max_batch=max_batch(),
    )


# ----------------------------------------------------------------------
# Review — the dry run. Changes nothing.
# ----------------------------------------------------------------------
@dataclass
class Skipped:
    label: str
    reasons: list


@dataclass
class Review:
    rows: list
    skipped: list
    requested: int
    limit: int

    @property
    def over_limit(self) -> bool:
        return self.requested > self.limit

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def phrase(self) -> str:
        return confirm_phrase(self.count)

    @property
    def vibes_deleted(self) -> int:
        return sum(r.vibes_total - r.vibes_sold for r in self.rows)

    @property
    def vibes_kept(self) -> int:
        return sum(r.vibes_sold for r in self.rows)

    @property
    def remixes(self) -> int:
        return sum(r.remixed_by_others for r in self.rows)

    @property
    def stars(self) -> int:
        return sum(r.stars for r in self.rows)

    @property
    def only_spam(self) -> bool:
        """A batch of bots: their addresses bounce, and bounces cost us the
        sender reputation that password-reset mail depends on."""
        return bool(self.rows) and all(r.hold_reason == 'spam' for r in self.rows)


# ----------------------------------------------------------------------
# Hostile input. Everything below the page's own form is untrusted: a script, a
# tampered request, or a careless paste. Two rules keep it harmless.
#   1. Ids are plain ASCII digits that fit the database's integer column.
#      `int()` alone accepts "1_0", "+5" and Arabic-Indic digits, and a 30-digit
#      id makes the driver RAISE instead of matching nothing (an OverflowError on
#      SQLite, "integer out of range" on PostgreSQL) — which the role decorator
#      would then show as a 403 page.
#   2. Free text is printable. Control characters, bidi overrides and zero-width
#      characters are removed: a NUL byte makes PostgreSQL refuse the whole audit
#      row, a right-to-left override makes a log line read backwards, and a run of
#      zero-width spaces would pass the length check while saying nothing.
# ----------------------------------------------------------------------
_PLAIN_ID = re.compile(r'[0-9]{1,19}')


def _id_ceiling() -> int:
    """The largest primary key the database can hold (an int32 for auth_user on
    PostgreSQL, where an id above it cannot exist and must not reach a query)."""
    try:
        _low, high = connection.ops.integer_field_range(User._meta.pk.get_internal_type())
    except Exception:  # pragma: no cover - a backend without integer ranges
        high = None
    return high or 2 ** 63 - 1


def _printable(text) -> str:
    """`text` without control, format, surrogate, private-use or unassigned
    characters. Whitespace (tab, newline, NBSP ...) is kept for the caller to
    collapse, so two words never get glued together."""
    if text is None:
        return ''
    if not isinstance(text, str):
        text = str(text)
    return ''.join(
        ch for ch in text
        if ch.isspace() or not unicodedata.category(ch).startswith('C')
    )


def clean_text(text, *, limit: int) -> str:
    """One line of plain, visible text, at most `limit` characters."""
    return ' '.join(_printable(text).split())[:limit].rstrip()


def clean_ids(raw, *, limit=None):
    """Unique positive ints, first-seen order, every one a possible primary key.

    Stops reading one past `limit` so a crafted POST of a million ids costs a
    million string compares at most, never a million queries.
    """
    limit = max_batch() if limit is None else limit
    ceiling = _id_ceiling()
    seen, out = set(), []
    for value in raw or ():
        text = str(value).strip()
        if not _PLAIN_ID.fullmatch(text):
            continue
        pk = int(text)
        if 0 < pk <= ceiling and pk not in seen:
            seen.add(pk)
            out.append(pk)
            if len(out) > limit:
                break
    return out


def _missing_skips(pks):
    """Ids that are no longer in the held set: lifted/expired, or already gone."""
    names = dict(User.objects.filter(pk__in=pks).values_list('pk', 'username'))
    return [
        Skipped(
            f'@{names[pk]}' if pk in names else f'#{pk}',
            ['No longer in quarantine — nothing to delete.' if pk in names else 'Already deleted.'],
        )
        for pk in pks
    ]


def review(actor, ids, *, now=None) -> Review:
    """Who would go, who would be refused and why, and what it costs."""
    ids = clean_ids(ids)
    limit = max_batch()
    if not is_admin(actor) or not ids:
        return Review(rows=[], skipped=[], requested=len(ids), limit=limit)
    held = {u.pk: u for u in held_accounts(actor=actor, now=now).filter(pk__in=ids[:limit])}
    rows, skipped, missing = [], [], []
    for pk in ids[:limit]:
        user = held.get(pk)
        if user is None:
            missing.append(pk)
            continue
        user.reason_label = REASON_LABELS.get(user.hold_reason, user.hold_reason)
        user.blockers = blockers_for(user)
        if user.blockers:
            skipped.append(Skipped(f'@{user.username}', [text for _code, text in user.blockers]))
        else:
            rows.append(user)
    skipped.extend(_missing_skips(missing))
    return Review(rows=rows, skipped=skipped, requested=len(ids), limit=limit)


# ----------------------------------------------------------------------
# Delete — the only writer.
# ----------------------------------------------------------------------
@dataclass
class DeleteResult:
    error: str = ''
    batch: str = ''
    deleted: list = field(default_factory=list)       # ['@name', ...]
    skipped: list = field(default_factory=list)       # [Skipped]
    failed: list = field(default_factory=list)        # [Skipped]
    vibes_deleted: int = 0
    vibes_kept: int = 0
    notified: int = 0
    not_notified: int = 0

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class _Gone:
    pk: int
    username: str
    email: str
    reason_label: str
    vibes_deleted: int
    vibes_kept: int


def _clean_why(text) -> str:
    return clean_text(text, limit=REASON_MAX)


def _lock_holds(ids):
    """Row-lock the live holds of these accounts.

    Lifting, extending and answering an appeal all write the hold row, so
    holding its lock until commit keeps the verdict we just read true while
    we delete. (SQLite has no row locks; Django makes this a no-op there and
    its single-writer model gives the same guarantee.)
    """
    list(
        UserQuarantine.objects.select_for_update()
        .filter(user_id__in=ids, status='active')
        .values_list('pk', flat=True)
    )


def _delete_chunk(*, actor, ids, now, why, batch):
    """One transaction: re-check under lock, release sold vibes, delete, audit.

    Returns (gone, skipped). Raises on a database error so the caller can fall
    back to one-at-a-time — the transaction has already rolled back, so the
    audit rows and the deletes stand or fall together.
    """
    with transaction.atomic():
        _lock_holds(ids)
        held = {u.pk: u for u in held_accounts(actor=actor, now=now).filter(pk__in=ids)}
        ready, skipped, missing = [], [], []
        for pk in ids:
            user = held.get(pk)
            if user is None:
                missing.append(pk)
                continue
            blockers = blockers_for(user)
            if blockers:
                skipped.append(Skipped(f'@{user.username}', [text for _code, text in blockers]))
            else:
                ready.append(user)
        skipped.extend(_missing_skips(missing))

        gone = [
            _Gone(
                pk=u.pk, username=u.username, email=u.email or '',
                reason_label=REASON_LABELS.get(u.hold_reason, u.hold_reason),
                vibes_deleted=u.vibes_total - u.vibes_sold, vibes_kept=u.vibes_sold,
            )
            for u in ready
        ]
        if ready:
            release_accounts_projects(ready)
            User.objects.filter(pk__in=[u.pk for u in ready]).delete()
            AdminLog.objects.bulk_create([
                AdminLog(
                    actor=actor, action='delete_account',
                    target=(
                        f'@{g.username}: deleted ({g.reason_label}; {g.vibes_deleted} vibes deleted, '
                        f'{g.vibes_kept} kept for buyers) [{batch}] — {why}'
                    )[:200],
                )
                for g in gone
            ])
    return gone, skipped


def _delete_one_by_one(*, actor, ids, now, why, batch):
    """The fallback when a chunk failed: isolate the account that caused it."""
    gone, skipped, failed = [], [], []
    for pk in ids:
        try:
            g, s = _delete_chunk(actor=actor, ids=[pk], now=now, why=why, batch=batch)
        except Exception:
            logger.exception('bulk account delete failed for user id=%s batch=%s', pk, batch)
            name = User.objects.filter(pk=pk).values_list('username', flat=True).first()
            failed.append(Skipped(
                f'@{name}' if name else f'#{pk}',
                ['Could not be deleted (the database refused). Nothing was changed for this account.'],
            ))
            continue
        gone.extend(g)
        skipped.extend(s)
    return gone, skipped, failed


def delete_quarantined_accounts(*, actor, ids, reason, confirm, notify=True, now=None) -> DeleteResult:
    """Delete the reviewed accounts, or explain why nothing happened.

    Everything is checked before anything is written. Each account is then
    re-checked UNDER LOCK, inside the transaction that deletes it, because the
    state the operator reviewed can change while they typed the confirmation
    (an appeal arrives, another admin lifts the hold).
    """
    if not is_admin(actor):
        logger.warning('account clean-up refused: %r is not an admin', getattr(actor, 'username', None))
        return DeleteResult(error='Only admins can delete accounts.')
    limit = max_batch()
    ids = clean_ids(ids, limit=limit)
    if not ids:
        return DeleteResult(error='Select at least one account.')
    if len(ids) > limit:
        return DeleteResult(error=f'At most {limit} accounts per batch. Deselect some and run it again.')
    why = _clean_why(reason)
    if len(why) < REASON_MIN:
        return DeleteResult(error=f'Write a reason of at least {REASON_MIN} characters — the audit log keeps it.')
    if ' '.join((confirm or '').split()).upper() != confirm_phrase(len(ids)):
        return DeleteResult(error=f'Type {confirm_phrase(len(ids))} exactly to confirm.')

    now = now or timezone.now()
    batch = secrets.token_hex(3)
    result = DeleteResult(batch=batch)
    gone_all = []
    for start in range(0, len(ids), CHUNK_SIZE):
        chunk = ids[start:start + CHUNK_SIZE]
        try:
            gone, skipped = _delete_chunk(actor=actor, ids=chunk, now=now, why=why, batch=batch)
        except Exception:
            logger.exception('bulk account delete chunk failed batch=%s — retrying one by one', batch)
            gone, skipped, failed = _delete_one_by_one(actor=actor, ids=chunk, now=now, why=why, batch=batch)
            result.failed.extend(failed)
        gone_all.extend(gone)
        result.skipped.extend(skipped)

    result.deleted = [f'@{g.username}' for g in gone_all]
    result.vibes_deleted = sum(g.vibes_deleted for g in gone_all)
    result.vibes_kept = sum(g.vibes_kept for g in gone_all)

    try:
        AdminLog.objects.create(
            actor=actor, action='bulk_delete_accounts',
            target=(
                f'[{batch}] {len(result.deleted)} deleted, {len(result.skipped)} skipped, '
                f'{len(result.failed)} failed — {why}'
            )[:200],
        )
    except Exception:
        logger.exception('could not write the bulk-delete summary row batch=%s', batch)
    logger.info(
        'bulk account delete batch=%s actor=%r deleted=%s skipped=%s failed=%s',
        batch, getattr(actor, 'username', None), len(result.deleted), len(result.skipped), len(result.failed),
    )

    if notify and gone_all:
        notices = [g for g in gone_all if g.email]
        # After the commit, never inside it (users/roles.py): a rolled-back
        # delete must not have told anybody their account is gone.
        transaction.on_commit(lambda: _send_notices(notices, result))
    return result


# ----------------------------------------------------------------------
# The notice — best-effort and time-boxed.
# ----------------------------------------------------------------------
def _support_email() -> str:
    """The operator-managed contact address, if one is set (footer contacts)."""
    try:
        from .footer_contacts import public_footer_contacts
        return next((c['value'] for c in public_footer_contacts() if c.get('kind') == 'email'), '')
    except Exception:
        return ''


def _send_notices(notices, result) -> None:
    """One email per deleted person, stopped when the time budget is spent.

    Sending is sequential HTTP to the mail provider, so an unbounded loop
    would make the operator's request as slow as the provider is. What does
    not fit in the budget is counted and reported, never retried silently.
    """
    from .emails import send_generic_email

    site_url = getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za').rstrip('/')
    support = _support_email()
    started = time.monotonic()
    budget = notice_budget_seconds()
    for index, gone in enumerate(notices):
        if time.monotonic() - started > budget:
            result.not_notified += len(notices) - index
            break
        try:
            ctx = {'username': gone.username, 'reason': gone.reason_label, 'site_url': site_url,
                   'vibes_kept': gone.vibes_kept, 'support_email': support}
            sent = send_generic_email(
                gone.email, 'Your BlaqVibes account was removed',
                render_to_string('emails/account_removed.txt', ctx),
                render_to_string('emails/account_removed.html', ctx),
            )
        except Exception:
            logger.exception('account-removed notice failed for %r', gone.username)
            sent = False
        if sent:
            result.notified += 1
        else:
            result.not_notified += 1
