"""Taste learning — "this user is into games, put games in front".

Two halves:
  * `record()`   — write path. Called from views when a user interacts.
  * `affinities()` / `personalized_order()` — read path. Called by the feed.

5 Whys — why does the write path never block the request?

1. Why does recording have to be cheap? It hangs off view/star/download,
   i.e. the busiest actions on the site. A slow recorder makes every
   interaction slow.
2. Why one UPDATE ... or one INSERT, and nothing else? An affinity bump is
   a single bounded row per (user, kind). No aggregation, no scan, no
   second query — so cost is independent of how much history the user has.
3. Why swallow every exception? Taste is a nice-to-have. A user must never
   fail to download a ZIP they paid for because a preference counter had a
   deadlock.
4. Why de-duplicate views in the cache rather than the database? Repeated
   views of one page in a minute say nothing new, and a cache check costs
   no write at all — the cheapest way to drop the highest-volume, lowest-
   information event.
5. Why not push it all to Celery? Celery is optional in this deployment
   (CELERY_EAGER=1 locally) and a queue hop costs more than the single
   UPDATE it would defer. The heavy work — appeal scoring — is what goes
   to the queue; a one-row bump does not need to.
"""
import logging
import math

from django.db import transaction
from django.db.models import Case, F, FloatField, Value, When
from django.utils import timezone

from .models import KindAffinity
from .taxonomy import KIND_VALUES, coerce_kind

logger = logging.getLogger(__name__)

# A view of the same project by the same user inside this window is ignored.
VIEW_DEDUPE_SECONDS = 300
# Personalisation only kicks in once there is at least this much signal —
# below it we would be reordering the feed on a coin flip.
MIN_SIGNAL_EVENTS = 2
# How strongly affinity may reorder the feed, relative to appeal_score (0-100).
#
# 5 Whys on the value:
# 1. Why cap it at all? An uncapped boost makes the feed a single-kind echo
#    chamber — a game lover would never be shown anything else again.
# 2. Why exactly half the appeal scale? It is the only non-arbitrary point:
#    a favourite kind can beat anything in the *better half* of the catalog,
#    and still loses to something outstanding in a kind they ignore.
# 3. Why not a small nudge like 10? Then taste would only ever reorder
#    near-ties, and the user's actual request — games in front — would not
#    visibly happen.
# 4. Why is it a constant and not per-user? A user with more history is not
#    entitled to a more distorted feed, only a more accurate one; the
#    normalisation in normalized_affinities already handles history size.
# 5. Why apply it multiplied by the normalised score? A second-favourite
#    kind should get part of the lift, not all of it or none.
MAX_AFFINITY_BOOST = 50.0


def _decayed(score, updated_at, now=None):
    """Exponential decay to `now`. Pure function, easy to test."""
    if not score:
        return 0.0
    now = now or timezone.now()
    try:
        days = max(0.0, (now - updated_at).total_seconds() / 86400.0)
    except Exception:
        return float(score)
    return float(score) * math.pow(0.5, days / KindAffinity.HALF_LIFE_DAYS)


def record(user, kind, event, project=None):
    """Bump one affinity row. Never raises, never blocks.

    `kind` may be a kind string or an AppProject (convenience for views).
    """
    try:
        if user is None or not getattr(user, 'is_authenticated', False):
            return False
        weight = KindAffinity.EVENT_WEIGHTS.get(event)
        if not weight:
            return False
        if hasattr(kind, 'kind'):
            project = project or kind
            kind = kind.kind
        kind = coerce_kind(kind)

        # Don't learn from your own vibes' views — an author refreshing
        # their page is not evidence of taste, it is anxiety.
        if project is not None and getattr(project, 'owner_id', None) == getattr(user, 'id', None):
            if event != 'publish':
                return False

        if event == 'view' and project is not None:
            from django.core.cache import cache
            key = f'taste:view:{user.pk}:{getattr(project, "pk", kind)}'
            if not cache.add(key, 1, VIEW_DEDUPE_SECONDS):
                return False

        now = timezone.now()
        with transaction.atomic():
            row, created = KindAffinity.objects.select_for_update().get_or_create(
                user=user, kind=kind,
                defaults={'score': weight, 'events': 1, 'last_event': event},
            )
            if not created:
                # Decay the stored value to now *before* adding, so old
                # score and new event are on the same time base.
                row.score = _decayed(row.score, row.updated_at, now) + weight
                row.events = (row.events or 0) + 1
                row.last_event = event
                row.save(update_fields=['score', 'events', 'last_event', 'updated_at'])
        return True
    except Exception:
        logger.exception('taste.record failed (user=%s kind=%s event=%s)',
                         getattr(user, 'pk', None), kind, event)
        return False


def record_many(user, kinds, event):
    for k in kinds:
        record(user, k, event)


def affinities(user, now=None):
    """{kind: decayed_score} for a user. {} for anonymous / no signal."""
    try:
        if user is None or not getattr(user, 'is_authenticated', False):
            return {}
        now = now or timezone.now()
        out = {}
        for row in user.kind_affinities.all():
            val = _decayed(row.score, row.updated_at, now)
            if val > 0.05:
                out[row.kind] = val
        return out
    except Exception:
        logger.exception('taste.affinities failed')
        return {}


def normalized_affinities(user, now=None):
    """Affinities scaled to 0..1 against the user's own top kind.

    Why normalise per user and not globally? A heavy user with 400 points
    of "game" and a new user with 8 both mean the same thing: games are
    their favourite. Absolute scores would let the heavy user's preferences
    dominate a ranking formula that is only ever applied to their own feed
    anyway.
    """
    raw = affinities(user, now=now)
    if not raw:
        return {}
    top = max(raw.values())
    if top <= 0:
        return {}
    return {k: round(v / top, 4) for k, v in raw.items()}


def has_enough_signal(user):
    try:
        if user is None or not getattr(user, 'is_authenticated', False):
            return False
        total = sum(r.events or 0 for r in user.kind_affinities.all()[:len(KIND_VALUES)])
        return total >= MIN_SIGNAL_EVENTS
    except Exception:
        return False


def top_kinds(user, limit=3):
    ranked = sorted(affinities(user).items(), key=lambda kv: kv[1], reverse=True)
    return [k for k, _ in ranked[:limit]]


def personalized_order(qs, user, base_field='appeal_score'):
    """Annotate `personal_score` and order by it, entirely in SQL.

    5 Whys — why a CASE expression instead of sorting in Python?

    1. Why not fetch and sort in Python? Ordering must apply to the whole
       result set *before* pagination; sorting in Python means loading
       every published vibe into memory to render 12 of them.
    2. Why CASE and not a join to KindAffinity? The user has at most 14
       affinity rows and the map is already in memory; inlining them as
       constants avoids a join and keeps the plan a single index scan.
    3. Why bounded by MAX_AFFINITY_BOOST? Personalisation should reorder
       within "good stuff", not resurface a terrible game above an
       excellent one. Capping the boost keeps quality in the equation.
    4. Why keep `appeal_score` as the base at all? A brand-new user has no
       affinities; the same query must still return a sensible global feed.
    5. Why annotate a named field? The template shows a "for you" reason
       chip, and tests assert on the ordering value directly.
    """
    try:
        norm = normalized_affinities(user)
        if not norm or not has_enough_signal(user):
            return qs.order_by(f'-{base_field}', '-created_at'), {}
        whens = [
            When(kind=k, then=Value(round(v * MAX_AFFINITY_BOOST, 4)))
            for k, v in norm.items() if v > 0
        ]
        if not whens:
            return qs.order_by(f'-{base_field}', '-created_at'), {}
        qs = qs.annotate(
            affinity_boost=Case(*whens, default=Value(0.0), output_field=FloatField())
        ).annotate(
            personal_score=F(base_field) + F('affinity_boost')
        ).order_by('-personal_score', '-created_at')
        return qs, norm
    except Exception:
        logger.exception('taste.personalized_order failed')
        return qs.order_by(f'-{base_field}', '-created_at'), {}
