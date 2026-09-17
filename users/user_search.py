"""Find a person — the first half of every admin action.

The problem this solves is not "search". It is: an operator has a name from
Slack, a support ticket or a police-ish request, and needs to be *sure* they
are about to change the right account. At 10k+ users a wrong pick is a
privilege escalation handed to the wrong human.

5 Whys — why a ranked search box instead of the old "list every user" table?

1. Why not the list? It was `User.objects.all()` ordered by username with one
   `<select>` per row: unbounded work (the page grows with the company), and
   finding "@kwame" in 40k rows on a phone is hopeless. Worse, every row was
   an armed weapon — one stray click on a 40k-row list changes a stranger.
2. Why ranked, not just filtered? The operator's input has no consistent
   shape: `@kwame`, `kwame`, `kwame@mail.com`, `Kwame`, `kwam`. Ranked
   matching means all of them land on the same row, first.
3. Why match ALL terms (`kwame 2024`)? A human types extra words to narrow,
   not to widen. AND-ing terms is what they expect from every other search box.
4. Why is an exact hit allowed to skip the list? Because it removes a whole
   decision: typing the handle you already know should take you to the
   person, not to a one-row list you then have to click.
5. Why bounded and counted? `[:20]` keeps the query and the page constant no
   matter how large the user table gets, and the honest "showing 20 of 340
   matches — refine" line stops an operator from believing they are looking at
   everything, which is the one thing a partial list must never imply.

What is deliberately NOT here:

* No typo tolerance on SQLite/MySQL. `difflib` over every username means
  loading every username — the exact unbounded work we removed. Postgres gets
  trigram matching (pg_trgm), which the database can do inside an index.
* No email search for non-superadmins. Emails are personal data; this module
  is only ever called from `@superadmin_required` views. Keep it that way.
* No fuzzy "did you mean" guessing on gold accounts. A wrong guess about
  identity is worse than no result.
"""
import logging

from django.contrib.auth.models import User
from django.db import connection
from django.db.models import (
    Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When,
)

from .roles import ROLE_ORDER

logger = logging.getLogger(__name__)

MIN_QUERY = 2
MAX_QUERY = 80
RESULT_LIMIT = 20
ROLE_LIST_LIMIT = 50
# Above this score the match is the identifier itself (exact @username,
# email or verified alternative email), so the caller may jump straight to
# the person instead of showing a one-row list.
EXACT_SCORE = 88
TRIGRAM_MIN = 0.45

try:  # allauth is installed in this project; the module still works without it
    from allauth.account.models import EmailAddress
except Exception:  # pragma: no cover - defensive
    EmailAddress = None


class UserSearchResults:
    """What a search found. `users` is a list, never an open-ended queryset —
    the page is bounded even when the user table is not."""

    def __init__(self, query, users=None, total=0, limit=RESULT_LIMIT, too_short=False):
        self.query = query
        self.users = users or []
        self.total = total
        self.limit = limit
        self.too_short = too_short

    @property
    def exhausted(self):
        """Everything that matched is on screen — no 'refine' hint needed."""
        return len(self.users) >= self.total

    @property
    def single_exact(self):
        """The one user the operator almost certainly meant, or None."""
        if self.total != 1 or len(self.users) != 1:
            return None
        user = self.users[0]
        return user if getattr(user, 'match_score', 0) >= EXACT_SCORE else None


def normalize(query):
    """`  @Kwame   Admin ` → `Kwame Admin`. Leading '@' is how people paste a
    handle; the column stores it without."""
    q = ' '.join((query or '').split())[:MAX_QUERY]
    return q[1:] if q.startswith('@') else q


def _terms(q):
    """Terms the query must ALL match. One-char terms (`a b`) would match half
    the table, so they are dropped from the AND — the ranking still uses them."""
    return [t for t in q.lower().split() if len(t) >= 2]


def _match_q(term):
    """One term matched against the identifiers an operator could have."""
    q = Q(username__icontains=term) | Q(email__icontains=term)
    if EmailAddress is not None:
        q |= Exists(EmailAddress.objects.filter(user=OuterRef('pk'), email__icontains=term))
    return q


def _score_fields(q):
    """Ranking, expressed once. Weight = how much the match tells us.

    Cumulative on purpose: `kwam` scores prefix(60)+contains(30)=90 for a
    username that starts with it, and 30 for one that merely contains it, so
    prefix hits always sort above interior hits without a second pass.
    """
    fields = {
        'score_username_exact': 100,
        'score_email_exact': 90,
        'score_alt_email_exact': 88,
        'score_username_prefix': 60,
        'score_email_prefix': 55,
        'score_alt_email_prefix': 52,
        'score_username_contains': 30,
        'score_alt_email_contains': 22,
        'score_email_contains': 20,
    }
    conditions = {
        'score_username_exact': Q(username__iexact=q),
        'score_email_exact': Q(email__iexact=q),
        'score_username_prefix': Q(username__istartswith=q),
        'score_email_prefix': Q(email__istartswith=q),
        'score_username_contains': Q(username__icontains=q),
        'score_email_contains': Q(email__icontains=q),
    }
    if EmailAddress is not None:
        conditions['score_alt_email_exact'] = Exists(
            EmailAddress.objects.filter(user=OuterRef('pk'), email__iexact=q))
        conditions['score_alt_email_prefix'] = Exists(
            EmailAddress.objects.filter(user=OuterRef('pk'), email__istartswith=q))
        conditions['score_alt_email_contains'] = Exists(
            EmailAddress.objects.filter(user=OuterRef('pk'), email__icontains=q))

    annotations = {}
    for name, weight in fields.items():
        condition = conditions.get(name)
        if condition is None:
            continue
        annotations[name] = Case(
            When(condition, then=Value(weight)),
            default=Value(0),
            output_field=IntegerField(),
        )
    return annotations


def _expression_sum(names):
    """F(a)+F(b)+… as one expression, starting from an integer 0 so the sum is
    an IntegerField even when a database has to type it."""
    return sum((F(name) for name in names), Value(0))


# Which SQL features this database can actually do. Probed once: a missing
# pg_trgm extension must cost one failed query per process, not per search.
_TRIGRAM = {'checked': False, 'ok': False}


def _trigram_available():
    if not _TRIGRAM['checked']:
        _TRIGRAM['checked'] = True
        _TRIGRAM['ok'] = connection.vendor == 'postgresql'
    return _TRIGRAM['ok']


def _search_users_sql(q, *, use_trigram):
    """The queryset. `use_trigram` is decided by the probe above/fallback."""
    annotations = _score_fields(q)
    annotations['match_score'] = _expression_sum(
        [name for name in annotations if name.startswith('score_')]
    )
    annotations['project_count'] = Count('projects', distinct=True)

    qs = User.objects.select_related('profile').annotate(**annotations)

    matched = Q(match_score__gt=0)
    if use_trigram:
        from django.contrib.postgres.search import TrigramSimilarity
        from django.db.models.functions import Greatest
        qs = qs.annotate(trigram=Greatest(
            TrigramSimilarity('username', q), TrigramSimilarity('email', q)))
        matched |= Q(trigram__gte=TRIGRAM_MIN)

    # Every term must match something — that is what "narrow it down" means to
    # a human. Rows that survive that filter have already earned their place,
    # so they are not additionally asked to contain the whole query as one
    # string ("kwame ghana" is not a substring of "kwame.ghana").
    terms = _terms(q)
    if len(terms) > 1:
        for term in terms:
            qs = qs.filter(_match_q(term))
        return qs.order_by('-match_score', 'username')

    # One term: score it, and on Postgres let trigram similarity rescue typos.
    ordering = ['-match_score', '-trigram', 'username'] if use_trigram \
        else ['-match_score', 'username']
    return qs.filter(matched).order_by(*ordering)


def search_users(query, *, limit=RESULT_LIMIT):
    """Ranked, bounded user search.

    Returns `UserSearchResults`; on a database without pg_trgm it silently
    falls back to the portable contains/prefix path (and remembers that).
    """
    q = normalize(query)
    if len(q) < MIN_QUERY:
        return UserSearchResults(q, too_short=bool(query and query.strip()), limit=limit)

    rows = None
    if _trigram_available():
        try:
            qs = _search_users_sql(q, use_trigram=True)
            rows = list(qs[:limit])
            total = qs.count()
        except Exception as exc:  # missing pg_trgm, a bad GIN index, a typo in
            # the planner — all of it degrades to the portable path instead of
            # a 500 on an admin page.
            logger.warning('trigram user search unavailable (%s) — using contains', exc)
            _TRIGRAM['ok'] = False
            rows = None

    if rows is None:
        qs = _search_users_sql(q, use_trigram=False)
        rows = list(qs[:limit])
        total = qs.count()

    _label_matches(rows, q)
    return UserSearchResults(q, rows, total=total, limit=limit)


def _label_matches(users, q):
    """Why each row is on the page ('email', 'name prefix', …). Computed in
    Python for the ≤20 rows already fetched — one query for every alternative
    address, no N+1, and no second round of SQL to explain the first."""
    alt_emails = {}
    if EmailAddress is not None and users:
        ids = [u.pk for u in users]
        for user_id, email in EmailAddress.objects.filter(
                user_id__in=ids).values_list('user_id', 'email'):
            alt_emails.setdefault(user_id, set()).add((email or '').lower())

    needle = q.lower()
    for user in users:
        username = (user.username or '').lower()
        email = (user.email or '').lower()
        alts = alt_emails.get(user.pk, set())

        if username == needle:
            user.match_label = 'exact username'
        elif email and email == needle:
            user.match_label = 'exact email'
        elif needle in alts:
            user.match_label = 'verified email address'
        elif username.startswith(needle):
            user.match_label = 'username starts with'
        elif email.startswith(needle):
            user.match_label = 'email starts with'
        elif any(a.startswith(needle) for a in alts):
            user.match_label = 'email address starts with'
        elif needle in username:
            user.match_label = 'username contains'
        elif email and needle in email:
            user.match_label = 'email contains'
        elif any(needle in a for a in alts):
            user.match_label = 'email address contains'
        else:
            # Nothing contains it — the row is here because pg_trgm found the
            # spelling close enough. Say so; a search that explains itself is
            # how an operator learns to trust the result.
            user.match_label = 'close match (typo)'


def users_with_role(role, *, limit=ROLE_LIST_LIMIT):
    """Everyone currently holding one role — the access-review view.

    Bounded like search: an operator asking "who is admin?" needs the answer
    even if it is 4,000 rows long, but the page must not try to render 4,000
    rows. Returns `(rows, total)`.
    """
    if role not in ROLE_ORDER:
        return [], 0
    qs = (User.objects.filter(profile__role=role)
          .select_related('profile')
          .annotate(project_count=Count('projects', distinct=True))
          .order_by(F('last_login').desc(nulls_last=True), 'username'))
    return list(qs[:limit]), qs.count()


ELEVATED_ROLES = ('superadmin', 'admin', 'moderator')


def elevated_rows(limit=ROLE_LIST_LIMIT):
    """Who can currently touch other people's work — highest role first.

    This is the view an operator actually needs when they open the page with
    nothing in mind: not 40,000 users, but the ~5 accounts with power.
    """
    rank = {slug: index for index, slug in enumerate(reversed(ELEVATED_ROLES))}
    ordering = Case(
        *[When(profile__role=slug, then=Value(order))
          for slug, order in rank.items()],
        default=Value(99),
        output_field=IntegerField(),
    )
    qs = (User.objects.filter(profile__role__in=ELEVATED_ROLES)
          .select_related('profile')
          .annotate(role_rank=ordering, project_count=Count('projects', distinct=True))
          .order_by('role_rank', 'username'))
    return list(qs[:limit])


def role_totals():
    """How many people hold each role — one aggregate, no user rows."""
    from .models import Profile
    counts = {slug: 0 for slug in ROLE_ORDER}
    for row in Profile.objects.values('role').annotate(n=Count('id')):
        if row['role'] in counts:
            counts[row['role']] = row['n']
    return counts
