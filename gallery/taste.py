"""Taste learning — "this user is into games, put games in front".
  * `record()`   — write path. Called from views when a user interacts.
  * `affinities()` / `personalized_order()` — read path. Called by the feed.
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
