"""Trending, rising creators and suggestions — "what's happening now".

Every number here comes from an append-only event row (Star, CloneEvent,
Trade, Comment), never from a cumulative counter. 5 Whys:

1. Why not order by `AppProject.stars`? That counter never decays, so page
   one would freeze forever and nothing new could ever be discovered — the
   exact complaint people have about "top" pages.
2. Why a fixed window instead of all time? Trending is a *rate*: what moved
   this week. A 7-day window is long enough to be meaningful and short
   enough that yesterday's upload can win.
3. Why several small aggregate queries instead of one big join? Each one is
   an indexed COUNT over a single table; combining them in Python keeps the
   query count constant (five) instead of multiplying per vibe.
4. Why is nothing cached? These are five bounded COUNTs. A cache would make
   the rails lie (a star that does not show for five minutes) and would
   make tests depend on clock time. Revisit if the tables hit millions.
5. Why exclude your own vibes from "rising creators"? Recommending yourself
   is noise; the rail exists to surface people you have not met.
"""
import logging
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from .models import AppProject, CloneEvent, Comment, Star, Trade

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7


def _window(days=WINDOW_DAYS):
    return timezone.now() - timedelta(days=days)


def trending_scores(days=WINDOW_DAYS, limit=30):
    """{project_id: score} for published vibes with activity in the window."""
    try:
        since = _window(days)
        scores = {}

        def bump(rows, weight):
            for project_id, n in rows:
                if project_id:
                    scores[project_id] = scores.get(project_id, 0) + n * weight

        # Weights mirror gallery.models.KindAffinity: paying (trade) proves
        # far more intent than scrolling past a card.
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
        return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit])
    except Exception:
        logger.exception('trending_scores failed')
        return {}


def trending_vibes(days=WINDOW_DAYS, limit=6, exclude_owner=None):
    """Published vibes ordered by this week's activity — newest ties first."""
    scores = trending_scores(days=days, limit=max(30, limit * 5))
    if not scores:
        # Nothing moved this week: fall back to the freshest vibes rather
        # than showing an empty rail. Honest — it says "new", not "hot".
        qs = AppProject.objects.filter(status='published')
        if exclude_owner:
            qs = qs.exclude(owner=exclude_owner)
        return list(qs.select_related('owner', 'owner__profile').order_by('-created_at')[:limit]), False
    qs = AppProject.objects.filter(status='published', id__in=list(scores.keys()))
    if exclude_owner:
        qs = qs.exclude(owner=exclude_owner)
    vibes = list(qs.select_related('owner', 'owner__profile'))
    vibes.sort(key=lambda p: (-scores.get(p.id, 0), -p.id))
    return vibes[:limit], True


def rising_creators(days=WINDOW_DAYS, limit=5, exclude_user=None):
    """Creators whose vibes were starred/traded most in the window."""
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
        return out
    except Exception:
        logger.exception('rising_creators failed')
        return []


def recent_remixes(limit=4):
    """Freshly published forks — the remix half of the loop, made visible."""
    try:
        return list(
            AppProject.objects.filter(status='published', forked_from__isnull=False)
            .select_related('owner', 'owner__profile', 'forked_from')
            .order_by('-created_at')[:limit]
        )
    except Exception:
        logger.exception('recent_remixes failed')
        return []


def suggested_creators(user, limit=4):
    """Creators to follow: active publishers you do not follow yet.

    Never returns the requesting user, and never returns someone they
    already follow — a follow button that does nothing is worse than no
    suggestion at all.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return []
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
        return list(User.objects.filter(id__in=ids).select_related('profile'))
    except Exception:
        logger.exception('suggested_creators failed')
        return []


def activity_summary(days=WINDOW_DAYS):
    """Tiny counts for the 'what's happening' strip."""
    try:
        since = _window(days)
        return {
            'published': AppProject.objects.filter(status='published', created_at__gte=since).count(),
            'stars': Star.objects.filter(created_at__gte=since).count(),
            'downloads': CloneEvent.objects.filter(created_at__gte=since).count(),
            'trades': Trade.objects.filter(created_at__gte=since).count(),
            'days': days,
        }
    except Exception:
        logger.exception('activity_summary failed')
        return {'published': 0, 'stars': 0, 'downloads': 0, 'trades': 0, 'days': days}
