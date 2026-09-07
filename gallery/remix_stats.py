"""Remix as a first-class data relationship (§3 / §18).

Everything in this module is derived from one column — `AppProject.forked_from`
— and one status filter. No score is invented, no ranking is editorial:

* **Original projects** — published projects with no source project.
* **Remixes** — published projects that have one.
* **Remix depth** — how many generations a family reached.
* **Most remixed** — families ranked by how many published remixes exist.
* **Fastest-growing families** — remixes published inside a recent window.
* **Top remixers** — builders who publish remixes of other people's work.

Visibility rule everywhere: `status='published'`. An unpublished remix is
invisible to strangers, so it can never leak through a leaderboard either.
Every function is crush-safe (returns an empty/zero shape on failure): a
discovery rail must never take the page down.
"""
import logging
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import AppProject

logger = logging.getLogger(__name__)

WINDOW_DAYS = 14
MAX_WALK = 12  # cycle/again-and-again guard for lineage walks


def _published():
    return AppProject.objects.filter(status='published')


def root_id_of(project, cache=None):
    """Id of the original at the top of this project's family.

    `cache` (dict) lets a caller resolve a whole page of projects without
    re-walking shared ancestors.
    """
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
    """The headline counts for the Discover page — originals vs remixes."""
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
        return {
            'projects': total,
            'originals': total - remixes,
            'remixes': remixes,
            'deepest_generation': deepest,
            'remix_share': round((remixes / total) * 100) if total else 0,
        }
    except Exception:
        logger.exception('remix_totals failed')
        return {'projects': 0, 'originals': 0, 'remixes': 0, 'deepest_generation': 0, 'remix_share': 0}


def most_remixed(limit=6):
    """Projects other builders actually built on, most remixed first."""
    try:
        return list(
            _published()
            .annotate(remix_count=Count('forks', filter=Q(forks__status='published')))
            .filter(remix_count__gt=0)
            .select_related('owner', 'owner__profile')
            .order_by('-remix_count', '-stars', '-created_at')[:limit]
        )
    except Exception:
        logger.exception('most_remixed failed')
        return []


def fastest_growing_families(limit=5, days=WINDOW_DAYS):
    """Families that gained the most published remixes in the window.

    A "family" is keyed by its original project, so a remix-of-a-remix
    counts towards the idea it descends from — that is the unit a reader
    cares about ("this idea is spreading"), not the intermediate node.
    """
    try:
        since = timezone.now() - timedelta(days=days)
        recent = list(
            _published()
            .filter(forked_from__isnull=False, created_at__gte=since)
            .select_related('forked_from__forked_from__forked_from', 'owner')[:300]
        )
        if not recent:
            return []
        cache, growth, builders = {}, {}, {}
        for project in recent:
            root = root_id_of(project, cache)
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
                continue  # original was unpublished/removed — stay quiet
            out.append({
                'project': root,
                'new_remixes': gained,
                'builders': len(builders.get(root_pk, ())),
                'days': days,
            })
        return out
    except Exception:
        logger.exception('fastest_growing_families failed')
        return []


def top_remixers(limit=6, days=None):
    """Builders who publish remixes of OTHER people's projects.

    Remixing your own work is legitimate building, but it is not the
    social act this rail is about, so it does not count here.
    """
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
        return out
    except Exception:
        logger.exception('top_remixers failed')
        return []


def fresh_remixes(limit=6):
    """The newest published remixes — "@someone remixed X by @origin"."""
    try:
        return list(
            _published()
            .filter(forked_from__isnull=False)
            .select_related('owner', 'owner__profile', 'forked_from', 'forked_from__owner')
            .order_by('-created_at')[:limit]
        )
    except Exception:
        logger.exception('fresh_remixes failed')
        return []


def remixable_projects(user=None, limit=8):
    """Good candidates to remix right now: published, downloadable, alive.

    Excludes the visitor's own work — the Build page asks "what will you
    build on?", and remixing yourself is not discovery.
    """
    try:
        qs = (
            _published()
            .annotate(remix_count=Count('forks', filter=Q(forks__status='published')))
            .select_related('owner', 'owner__profile')
            .order_by('-appeal_score', '-stars', '-created_at')
        )
        if user is not None and getattr(user, 'is_authenticated', False):
            qs = qs.exclude(owner=user)
        return list(qs[:limit])
    except Exception:
        logger.exception('remixable_projects failed')
        return []
