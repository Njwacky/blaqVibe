"""Remix stats — cached for performance, same semantics as before."""
import logging
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import AppProject

logger = logging.getLogger(__name__)

WINDOW_DAYS = 14
MAX_WALK = 12

def _published():
    return AppProject.objects.filter(status='published')

def root_id_of(project, cache=None):
    if cache is not None and project.pk in cache:
        return cache[project.pk]
    node, walked, seen = project, 0, {project.pk}
    while node.forked_from_id and walked < MAX_WALK and node.forked_from_id not in seen:
        parent = node.forked_from
        if parent is None:
            break
        node = parent
        seen.add(node.pk)
        walked += 1
    if cache is not None:
        for pk in seen:
            cache[pk] = node.pk
    return node.pk

def remix_totals():
    """Headline counts — cached 5 min."""
    cache_key = "remix:totals:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        published = _published()
        total = published.count()
        remixes = published.filter(forked_from__isnull=False).count()
        deepest = 0
        for project in (
            published.filter(forked_from__isnull=False)
            .select_related('forked_from__forked_from__forked_from')[:200]
        ):
            deepest = max(deepest, project.remix_generation)
        result = {
            'projects': total,
            'originals': total - remixes,
            'remixes': remixes,
            'deepest_generation': deepest,
            'remix_share': round((remixes / total) * 100) if total else 0,
        }
        try:
            cache.set(cache_key, result, 300)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('remix_totals failed')
        return {'projects': 0, 'originals': 0, 'remixes': 0, 'deepest_generation': 0, 'remix_share': 0}

def most_remixed(limit=6):
    cache_key = f"remix:most:l{limit}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        result = list(
            _published()
            .annotate(remix_count=Count('forks', filter=Q(forks__status='published')))
            .filter(remix_count__gt=0)
            .select_related('owner', 'owner__profile')
            .order_by('-remix_count', '-stars', '-created_at')[:limit]
        )
        try:
            cache.set(cache_key, result, 180)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('most_remixed failed')
        return []

def fastest_growing_families(limit=5, days=WINDOW_DAYS):
    cache_key = f"remix:growing:l{limit}:d{days}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        since = timezone.now() - timedelta(days=days)
        recent = list(
            _published()
            .filter(forked_from__isnull=False, created_at__gte=since)
            .select_related('forked_from__forked_from__forked_from', 'owner')[:300]
        )
        if not recent:
            return []
        c, growth, builders = {}, {}, {}
        for project in recent:
            root = root_id_of(project, c)
            growth[root] = growth.get(root, 0) + 1
            builders.setdefault(root, set()).add(project.owner_id)
        ordered = sorted(growth.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        roots = {
            p.pk: p for p in _published()
            .filter(pk__in=[pk for pk, _ in ordered])
            .select_related('owner', 'owner__profile')
        }
        out = []
        for root_pk, gained in ordered:
            root = roots.get(root_pk)
            if root is None:
                continue
            out.append({
                'project': root,
                'new_remixes': gained,
                'builders': len(builders.get(root_pk, ())),
                'days': days,
            })
        try:
            cache.set(cache_key, out, 180)
        except Exception:
            pass
        return out
    except Exception:
        logger.exception('fastest_growing_families failed')
        return []

def top_remixers(limit=6, days=None):
    cache_key = f"remix:top_remixers:l{limit}:d{days}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        qs = _published().filter(forked_from__isnull=False).exclude(
            forked_from__owner_id=F('owner_id')
        )
        if days:
            qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
        rows = (
            qs.values('owner_id')
            .annotate(n=Count('id'))
            .order_by('-n')[:limit]
        )
        users = {
            u.id: u for u in User.objects.filter(
                id__in=[r['owner_id'] for r in rows]
            ).select_related('profile')
        }
        out = []
        for row in rows:
            user = users.get(row['owner_id'])
            if user is not None:
                user.remix_count = row['n']
                out.append(user)
        try:
            cache.set(cache_key, out, 180)
        except Exception:
            pass
        return out
    except Exception:
        logger.exception('top_remixers failed')
        return []

def fresh_remixes(limit=6):
    cache_key = f"remix:fresh:l{limit}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        result = list(
            _published()
            .filter(forked_from__isnull=False)
            .select_related('owner', 'owner__profile', 'forked_from', 'forked_from__owner')
            .order_by('-created_at')[:limit]
        )
        try:
            cache.set(cache_key, result, 120)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('fresh_remixes failed')
        return []

def remixable_projects(user=None, limit=8):
    # Per-user exclusion, so cache key includes user id if present
    uid = getattr(user, 'pk', 0) if user and getattr(user, 'is_authenticated', False) else 0
    cache_key = f"remix:remixable:u{uid}:l{limit}:v2"
    try:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        qs = (
            _published()
            .annotate(remix_count=Count('forks', filter=Q(forks__status='published')))
            .select_related('owner', 'owner__profile')
            .order_by('-appeal_score', '-stars', '-created_at')
        )
        if user is not None and getattr(user, 'is_authenticated', False):
            qs = qs.exclude(owner=user)
        result = list(qs[:limit])
        try:
            cache.set(cache_key, result, 180)
        except Exception:
            pass
        return result
    except Exception:
        logger.exception('remixable_projects failed')
        return []
