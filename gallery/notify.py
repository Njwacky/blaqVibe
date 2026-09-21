"""In-app notifications: the only creation path, plus the severity table.

Why the severity table lives HERE and not in the template:
1. One writer. `notify()` is already the single door into the inbox, so the
   kind→category mapping sitting next to it means a caller cannot create a
   row that has no colour, and a template cannot invent one.
2. The colour is data, not decoration. The inbox SORTS on it (critical
   first) and the site-wide banner COUNTS on it, so it has to be a stored
   column with a server-side rank — a CSS class chosen in the template could
   never order a queryset.
3. Stability. `category` is written once at create time. When the map grows a
   new kind, yesterday's notifications keep the colour the person already saw.
"""
import logging

from django.db.models import Case, F, IntegerField, Value, When

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Severity categories — five, ordered by "deal with this first".
#
# `rank` is the sort key: the inbox and the attention centre both order by it,
# so a critical row outranks a newer social row. `swatch` is the raw colour for
# JSON/email surfaces that cannot read CSS variables; the on-site stripe uses
# the theme-aware `--cat-*` tokens in static/gallery/css/attention.css, so
# light mode gets a darker stripe for the same category.
# ----------------------------------------------------------------------
CATEGORY_META = {
    'critical': {
        'key': 'critical', 'label': 'Critical', 'rank': 0, 'swatch': '#F43F5E',
        'meaning': 'Blocked, unsafe, or about to be erased. Read this first.',
    },
    'action': {
        'key': 'action', 'label': 'Action needed', 'rank': 1, 'swatch': '#F59E0B',
        'meaning': 'A decision is waiting for you, with a deadline.',
    },
    'money': {
        'key': 'money', 'label': 'Money', 'rank': 2, 'swatch': '#10B981',
        'meaning': 'Stars or Rand moved. Receipts.',
    },
    'social': {
        'key': 'social', 'label': 'Social', 'rank': 3, 'swatch': '#38BDF8',
        'meaning': 'Somebody responded to your work.',
    },
    'system': {
        'key': 'system', 'label': 'System', 'rank': 4, 'swatch': '#8B8BA3',
        'meaning': 'Platform information. Nothing to do.',
    },
}
CATEGORY_ORDER = ['critical', 'action', 'money', 'social', 'system']

# Every Notification kind maps to exactly one category. Adding a kind without
# a row here is a bug the test suite catches (test_every_kind_has_a_category),
# because an unmapped kind would silently fall back to 'system' — the colour
# of "nothing to do" — and hide itself at the bottom of the inbox.
CATEGORY_OF_KIND = {
    # critical — something is blocked or will be destroyed
    'quarantined': 'critical',
    'account_quarantine': 'critical',
    'appeal': 'critical',
    'git_push_rejected': 'critical',
    # action — a human decision is waiting
    'duplicate': 'action',
    'malfunction': 'action',
    'approval': 'action',
    'pending': 'action',
    'review': 'action',
    'review_needed': 'action',
    'challenge_draft': 'action',
    'pr': 'action',
    'co_owner': 'action',
    'report': 'action',
    # money — stars and Rand
    'tip': 'money',
    'trade': 'money',
    'sale': 'money',
    'payout': 'money',
    # social — people responding to the work
    'comment': 'social',
    'follow': 'social',
    'star': 'social',
    'fork': 'social',
    'challenge': 'social',
    # A human answered (or is answering) you — feedback conversations.
    'feedback': 'social',
    # system — informational
    'published': 'system',
    'upload': 'system',
    'git_push': 'system',
    'milestone': 'system',
    'achievement': 'system',
    'role': 'system',
}

def category_for(kind: str) -> str:
    """The stored severity for a kind. Unknown kinds are 'system' — visible,
    never invisible, and the test suite pins the mapping so this stays rare."""
    return CATEGORY_OF_KIND.get(kind, 'system')

def category_meta(category: str) -> dict:
    """Fixed presentation row for one category (label, rank, swatch)."""
    return CATEGORY_META.get(category) or CATEGORY_META['system']

def severity_ordering():
    """A queryset expression that sorts critical-first without a CASE in SQL
    per category constant drifting from CATEGORY_META — the ranks come from the
    same table the templates read."""
    whens = [
        When(category=key, then=Value(meta['rank']))
        for key, meta in CATEGORY_META.items()
    ]
    return Case(*whens, default=Value(len(CATEGORY_ORDER)), output_field=IntegerField())

def inbox_queryset(user, limit=50):
    """The inbox as the person should read it: most severe first, newest
    within a severity next. `effective_at` (a reminder bump) beats created_at
    so a re-delivered decision rises back to the top of its own band."""
    from .models import Notification
    # `sorts_at`, not `effective_at`: Notification.effective_at is a PROPERTY on
    # the model, and annotating a queryset with the same name makes Django try to
    # assign the computed column onto a read-only attribute. The property reads
    # the row's own fields; this expression does the same thing in SQL.
    sorts_at = Case(
        When(reminded_at__isnull=False, then=F('reminded_at')),
        default=F('created_at'),
    )
    return (
        Notification.objects
        .filter(user=user)
        .annotate(severity_rank=severity_ordering(), sorts_at=sorts_at)
        .order_by('severity_rank', '-sorts_at', '-id')[:limit]
    )

def notify(user, kind, title, body='', url='', category=None, attention_case=None):
    """Create one inbox row. Returns the row, or None if it could not be made.

    `category` defaults to the kind's severity; a caller may override it when
    the SAME kind can be urgent or routine (a malfunction that merely needs a
    re-upload is 'action', one whose bytes were quarantined is 'critical').
    """
    if not user:
        return None
    try:
        from .models import Notification
        from .profanity import contains_profanity
        # An inbox is still a public-ish surface (the recipient did not
        # write the quote). Drop a body/title that slipped past a caller.
        if contains_profanity(body):
            body = ''
        if contains_profanity(title):
            title = 'New activity on BlaqVibes'
        return Notification.objects.create(
            user=user,
            kind=kind,
            category=(category or category_for(kind))[:10],
            title=title[:200],
            body=(body or '')[:400],
            url=(url or '')[:300],
            attention_case=attention_case,
        )
    except Exception:
        logger.exception('notify failed kind=%s', kind)
        return None

def redeliver(notification, title=None, body=None, category=None):
    """Re-deliver an existing row as a reminder — the 30-minute cadence.

    One row per case, bumped instead of appended, because a reminder that
    creates a new row every 30 minutes turns a 7-day deadline into 336 inbox
    entries and the person stops opening the inbox at all. Bumping keeps the
    history honest (`remind_count` on the case counts the nudges) and keeps
    the unread badge at 1 instead of climbing to hundreds.

    `is_read=False` is the point: an already-read decision becomes unread
    again, so the nav badge and the banner both come back.
    """
    if notification is None:
        return None
    try:
        from django.utils import timezone
        from .profanity import contains_profanity
        fields = ['is_read', 'reminded_at']
        notification.is_read = False
        notification.reminded_at = timezone.now()
        if title is not None and not contains_profanity(title):
            notification.title = title[:200]
            fields.append('title')
        if body is not None and not contains_profanity(body):
            notification.body = body[:400]
            fields.append('body')
        if category is not None:
            notification.category = category[:10]
            fields.append('category')
        notification.save(update_fields=fields)
        return notification
    except Exception:
        logger.exception('redeliver failed notification=%s', getattr(notification, 'pk', None))
        return None
