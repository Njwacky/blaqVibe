"""Trending, rising creators and suggestions — "what's happening now".
Every number here comes from an append-only event row (Star, CloneEvent,
Trade, Comment). Now cached to avoid 4-5 COUNT queries per feed load.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from .models import AppProject, CloneEvent, Comment, Star, Trade

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7

def _window(days=WINDOW_DAYS):
    return timezone.now() - timedelta(days=days)

def _cache_key(name, **kwargs):
    parts = [name, f"d{kwargs.get('days', WINDOW_DAYS)}", f"l{kwargs.get('limit', 6)}"]
    if kwargs.get('exclude_owner_id'):
        parts.append(f"o{kwargs['exclude_owner_id']}")
    # exclude_ids varies per request (grid ids) — don't include in cache key for scores,
    # but do for final list? We cache scores, not final filtered list.
    return f"trending:{':'.join(map(str, parts))}:v2"

def trending_scores(days=WINDOW_DAYS, limit=30):
    """{project_id: score} for published vibes with activity in the window. Cached 2 min."""
    cache_key = f"trending:scores:d{days}:l{limit}:v3"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass

    try:
        since = _window(days)
        scores = {}

        def bump(rows, weight):
            for project_id, n in rows:
                if project_id:
                    scores[project_id] = scores.get(project_id, 0) + n * weight

        # Weights mirror gallery.models.KindAffinity
        bump(
            Star.objects.filter(created_at__gte=since)
            .values_list('project_id').annotate(n=Count('id')), 3)
        bump(
            CloneEvent.objects.filter(created_at__gte=since)
            .values_list('project_id').annotate(n=Count('id')), 5)
        bump(
            Trade.objects.filter(created_at__gte=since)
            .values_list('project_id').annotate(n=Count('id')), 8)
        bump(
            Comment.objects.filter(created_at__gte=since, is_hidden=False)
            .values_list('project_id').annotate(n=Count('id')), 3)

        result = dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit])
        try:
            cache.set(cache_key, result, 120)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('trending_scores failed')
        return {}

def trending_vibes(days=WINDOW_DAYS, limit=6, exclude_owner=None, exclude_ids=()):
    """Published vibes ordered by this week's activity — cached scores, filtered per request."""
    scores = trending_scores(days=days, limit=max(30, limit * 5))
    if not scores:
        # Nothing moved this week: fall back to freshest vibes — cache 2 min
        cache_key = f"trending:fallback:l{limit}:o{getattr(exclude_owner, 'pk', 0)}:v2"
        try:
            hit = cache.get(cache_key)
            if hit is not None and not exclude_ids:
                return hit, False
        except Exception:
            pass
        qs = AppProject.objects.filter(status='published')
        if exclude_owner:
            qs = qs.exclude(owner=exclude_owner)
        if exclude_ids:
            qs = qs.exclude(id__in=list(exclude_ids))
        result = list(qs.select_related('owner', 'owner__profile').order_by('-created_at')[:limit])
        if not exclude_ids:
            try:
                cache.set(cache_key, result, 120)
            except Exception:
                pass
        return result, False

    qs = AppProject.objects.filter(status='published', id__in=list(scores.keys()))
    if exclude_owner:
        qs = qs.exclude(owner=exclude_owner)
    if exclude_ids:
        qs = qs.exclude(id__in=list(exclude_ids))
    vibes = list(qs.select_related('owner', 'owner__profile').only(
        'id', 'title', 'slug', 'stars', 'owner', 'created_at', 'status'
    ))
    # Need owner profile for template — select_related already does, but only() must include owner_id
    # Re-fetch with full select if only() breaks profile access (safe fallback)
    if vibes and not hasattr(vibes[0].owner, 'profile'):
        qs = AppProject.objects.filter(status='published', id__in=list(scores.keys()))
        if exclude_owner:
            qs = qs.exclude(owner=exclude_owner)
        if exclude_ids:
            qs = qs.exclude(id__in=list(exclude_ids))
        vibes = list(qs.select_related('owner', 'owner__profile'))
    vibes.sort(key=lambda p: (-scores.get(p.id, 0), -p.id))
    return vibes[:limit], True

def rising_creators(days=WINDOW_DAYS, limit=5, exclude_user=None):
    """Creators whose vibes were starred/traded most in the window. Cached 3 min."""
    cache_key = f"trending:rising:d{days}:l{limit}:o{getattr(exclude_user, 'pk', 0)}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        since = _window(days)
        rows = (
            Star.objects.filter(created_at__gte=since)
            .values('project__owner_id').annotate(n=Count('id'))
            .order_by('-n')[:limit * 3]
        )
        trades = (
            Trade.objects.filter(created_at__gte=since)
            .values('project__owner_id').annotate(n=Count('id'))
        )
        totals = {}
        for r in rows:
            uid = r.get('project__owner_id')
            if uid:
                totals[uid] = totals.get(uid, 0) + r['n']
        for r in trades:
            uid = r.get('project__owner_id')
            if uid:
                totals[uid] = totals.get(uid, 0) + r['n'] * 2
        if exclude_user and exclude_user.pk in totals:
            totals.pop(exclude_user.pk, None)
        ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        from django.contrib.auth.models import User
        users = {u.id: u for u in User.objects.filter(id__in=[u for u, _ in ordered])
                 .select_related('profile')}
        out = []
        for uid, score in ordered:
            user = users.get(uid)
            if user:
                user.recent_heat = score
                out.append(user)
        try:
            cache.set(cache_key, out, 180)
        except Exception:
            pass
        return out
    except Exception:
        logger.exception('rising_creators failed')
        return []

def recent_remixes(limit=4):
    """Freshly published forks — cached 2 min."""
    cache_key = f"trending:remixes:l{limit}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        result = list(
            AppProject.objects.filter(status='published', forked_from__isnull=False)
            .select_related('owner', 'owner__profile', 'forked_from')
            .order_by('-created_at')[:limit]
        )
        try:
            cache.set(cache_key, result, 120)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('recent_remixes failed')
        return []

def suggested_creators(user, limit=4):
    """Creators to follow — per-user, cache 5 min."""
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    cache_key = f"trending:suggested:{user.pk}:l{limit}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        following = set(user.following.values_list('following_id', flat=True))
        following.add(user.pk)
        rows = (
            AppProject.objects.filter(status='published')
            .exclude(owner_id__in=following)
            .values('owner_id').annotate(n=Count('id'), stars=Count('stars'))
            .order_by('-n')[:limit * 3]
        )
        ids = [r['owner_id'] for r in rows if r.get('owner_id')][:limit]
        from django.contrib.auth.models import User
        result = list(User.objects.filter(id__in=ids).select_related('profile'))
        try:
            cache.set(cache_key, result, 300)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('suggested_creators failed')
        return []

def activity_summary(days=WINDOW_DAYS):
    """Tiny counts for the 'what's happening' strip. Cached 3 min."""
    cache_key = f"trending:activity:d{days}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        since = _window(days)
        result = {
            'published': AppProject.objects.filter(status='published', created_at__gte=since).count(),
            'remixes': AppProject.objects.filter(
                status='published', forked_from__isnull=False, created_at__gte=since).count(),
            'stars': Star.objects.filter(created_at__gte=since).count(),
            'downloads': CloneEvent.objects.filter(created_at__gte=since).count(),
            'trades': Trade.objects.filter(created_at__gte=since).count(),
            'days': days,
        }
        try:
            cache.set(cache_key, result, 180)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('activity_summary failed')
        return {'published': 0, 'remixes': 0, 'stars': 0, 'downloads': 0, 'trades': 0, 'days': days}
