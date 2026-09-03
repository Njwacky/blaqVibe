"""Creator analytics — the numbers a creator would come back for.

Privacy rule: everything here is scoped to ONE project owned by the
caller, or aggregated across the caller's own vibes. There is no function
in this module that takes another user's id, so a view cannot accidentally
leak somebody else's numbers by passing the wrong argument.

5 Whys (why per-project stats live here and not on the detail page)
1. The detail page is public; views/followers/trade counts are not. Mixing
   them invites a template edit to leak a private number.
2. Why counts from event tables and not the cached counters?
   AppProject.views/clones/stars are cumulative integers with no dates, so
   they cannot answer "today" — the whole point of the page. Event rows
   (VibeView, CloneEvent, Star, Trade) carry timestamps.
3. Why 14 days? Long enough to see a trend, short enough to render as a
   bar you can read on a phone.
4. Why return plain dicts? The template draws them; the same dicts are
   what the tests assert on, without parsing HTML.
5. Why rank against the vibe's own kind? "You're #12 in Python projects"
   is a claim a creator can act on. A global rank against games, bots and
   extensions is noise.
"""
import logging
from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

logger = logging.getLogger(__name__)

WINDOW_DAYS = 14


def _daily_counts(queryset, date_field='created_at', days=WINDOW_DAYS):
    """[(date, count)] for the window, zero-filled (a chart needs gaps)."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = (
        queryset.filter(**{f'{date_field}__date__gte': start})
        .annotate(day=TruncDate(date_field))
        .values('day').annotate(n=Count('id')).order_by('day')
    )
    counts = {r['day']: r['n'] for r in rows if r['day']}
    return [(start + timedelta(days=i), counts.get(start + timedelta(days=i), 0))
            for i in range(days)]


def _in_window(queryset, date_field='created_at', days=1):
    since = timezone.now() - timedelta(days=days)
    return queryset.filter(**{f'{date_field}__gte': since}).count()


def _series_with_max(pairs):
    """([(date, n)], max) — the template needs the max to scale its bars."""
    return pairs, max((n for _d, n in pairs), default=0)


def project_stats(project, days=WINDOW_DAYS):
    """Everything a creator needs to know about one of their own vibes."""
    from .models import CloneEvent, Star, Trade, VibeView
    try:
        today = _in_window(VibeView.objects.filter(project=project), 'last_viewed', days=1)
        week_views = _in_window(VibeView.objects.filter(project=project), 'last_viewed', days=7)
        downloads_week = _in_window(CloneEvent.objects.filter(project=project), days=7)
        stars_week = _in_window(Star.objects.filter(project=project), days=7)
        trades = Trade.objects.filter(project=project).aggregate(
            n=Count('id'), stars=Sum('cost'))
        views_series, views_max = _series_with_max(_daily_counts(
            VibeView.objects.filter(project=project), 'last_viewed', days))
        downloads_series, downloads_max = _series_with_max(_daily_counts(
            CloneEvent.objects.filter(project=project), 'created_at', days))
        return {
            'project': project,
            'views_series': views_series,
            'views_max': views_max,
            'downloads_series': downloads_series,
            'downloads_max': downloads_max,
            'views_total': getattr(project, 'views', 0) or 0,
            'views_today': today,
            'views_week': week_views,
            'downloads_total': getattr(project, 'clones', 0) or 0,
            'downloads_week': downloads_week,
            'stars_total': getattr(project, 'stars', 0) or 0,
            'stars_week': stars_week,
            'trades_total': trades['n'] or 0,
            'stars_earned': trades['stars'] or 0,
            'forks': project.forks.count(),
            'comments': project.comments.filter(is_hidden=False).count(),
            'rank_in_kind': rank_in_kind(project),
            'days': days,
        }
    except Exception:
        logger.exception('project_stats failed %s', getattr(project, 'slug', '?'))
        return {'project': project, 'views_series': [], 'views_max': 0,
                'downloads_series': [], 'downloads_max': 0,
                'rank_in_kind': None, 'days': days}


def rank_in_kind(project):
    """1-based position of this vibe among published vibes of its kind."""
    from .models import AppProject
    try:
        ahead = AppProject.objects.filter(
            status='published', kind=project.kind, stars__gt=project.stars
        ).count()
        total = AppProject.objects.filter(status='published', kind=project.kind).count()
        return {'position': ahead + 1, 'of': total, 'kind': project.kind_label}
    except Exception:
        logger.debug('rank_in_kind failed %s', getattr(project, 'slug', '?'))
        return None


def creator_stats(user, days=WINDOW_DAYS):
    """Totals across everything the creator owns (owner-scoped only)."""
    from .models import AppProject, CloneEvent, Trade, VibeView
    try:
        vibes = AppProject.objects.filter(owner=user, status='published')
        ids = list(vibes.values_list('id', flat=True))
        return {
            'published': len(ids),
            'views_week': _in_window(
                VibeView.objects.filter(project_id__in=ids), 'last_viewed', 7),
            'downloads_week': _in_window(CloneEvent.objects.filter(project_id__in=ids), days=7),
            'stars_total': sum(getattr(v, 'stars', 0) or 0 for v in vibes),
            'trades_week': _in_window(Trade.objects.filter(project_id__in=ids), days=7),
            'stars_earned': Trade.objects.filter(seller=user).aggregate(s=Sum('cost'))['s'] or 0,
            'followers': user.followers.count(),
        }
    except Exception:
        logger.exception('creator_stats failed for %s', getattr(user, 'pk', None))
        return {}
