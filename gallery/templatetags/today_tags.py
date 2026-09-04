from datetime import timedelta

from django import template
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

register = template.Library()


@register.inclusion_tag('gallery/includes/today_loop.html', takes_context=True)
def today_loop(context):
    """Render a compact, cached creator command center for the unfiltered feed.

    The feed should feel personal without turning every request into a chain of
    social queries. The loop is intentionally short-lived: a creator can act,
    refresh, and see the new state without carrying stale data for long.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'today_enabled': False}

    cache_key = f'blaqvibes:today:v2:{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = {
        'today_enabled': True,
        'daily': None,
        'weekly_xp': 0,
        'rank': {'name': 'Bronze', 'threshold': 0},
        'my_vibes': [],
        'my_next_vibe': None,
        'unread_notifications': 0,
        'recent_notifications': [],
        'following_vibes': [],
        'week_stars': 0,
        'week_comments': 0,
        'week_reviews': 0,
        'next_action': 'Publish your first vibe',
        'next_action_url': '/publish/',
    }

    try:
        from gallery.daily import today_challenge
        data['daily'] = today_challenge()
    except Exception:
        pass

    since = timezone.now() - timedelta(days=7)

    try:
        from users.models import XPEvent
        data['weekly_xp'] = XPEvent.objects.filter(
            user=user, created_at__gte=since
        ).aggregate(total=Sum('amount'))['total'] or 0
    except Exception:
        pass

    try:
        from gallery.ranks import contributor_bonus
        data['rank'] = contributor_bonus(user)
    except Exception:
        pass

    try:
        from gallery.models import AppProject
        data['my_vibes'] = list(
            AppProject.objects.filter(owner=user, status='published')
            .only('id', 'title', 'slug', 'stars', 'updated_at')
            .order_by('-updated_at')[:3]
        )
        data['my_next_vibe'] = data['my_vibes'][0] if data['my_vibes'] else None
    except Exception:
        pass

    try:
        from gallery.models import Notification
        data['unread_notifications'] = Notification.objects.filter(
            user=user, is_read=False
        ).count()
        data['recent_notifications'] = list(
            Notification.objects.filter(user=user)
            .only('id', 'title', 'body', 'url', 'created_at', 'is_read')
            .order_by('-created_at')[:3]
        )
    except Exception:
        pass

    try:
        from users.models import Follow
        followed_ids = list(
            Follow.objects.filter(follower=user)
            .values_list('following_id', flat=True)[:50]
        )
        if followed_ids:
            from gallery.models import AppProject
            data['following_vibes'] = list(
                AppProject.objects.filter(
                    owner_id__in=followed_ids, status='published'
                )
                .select_related('owner')
                .only('id', 'title', 'slug', 'stars', 'created_at', 'owner__username')
                .order_by('-created_at')[:4]
            )
    except Exception:
        pass

    try:
        from gallery.models import AppProject, Comment, Review, Star
        project_ids = AppProject.objects.filter(
            owner=user, status='published'
        ).values_list('id', flat=True)
        data['week_stars'] = Star.objects.filter(
            project_id__in=project_ids, created_at__gte=since
        ).exclude(user=user).count()
        data['week_comments'] = Comment.objects.filter(
            project_id__in=project_ids, created_at__gte=since, is_hidden=False
        ).exclude(user=user).count()
        data['week_reviews'] = Review.objects.filter(
            project_id__in=project_ids, created_at__gte=since
        ).exclude(user=user).count()
    except Exception:
        pass

    if data['unread_notifications']:
        data['next_action'] = 'See what happened to your vibes'
        data['next_action_url'] = '/notifications/'
    elif data['week_stars'] or data['week_comments'] or data['week_reviews']:
        data['next_action'] = 'Turn feedback into your next remix'
        data['next_action_url'] = data['my_next_vibe'].get_absolute_url() if data['my_next_vibe'] else '/publish/'
    elif data['my_next_vibe']:
        data['next_action'] = 'Remix something and ship an update'
        data['next_action_url'] = data['my_next_vibe'].get_absolute_url()

    cache.set(cache_key, data, 30)
    return data
