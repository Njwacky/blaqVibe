from django import template
from django.core.cache import cache

register = template.Library()


@register.inclusion_tag('gallery/includes/today_loop.html', takes_context=True)
def today_loop(context):
    """Render a compact builder-first network pulse for authenticated users."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'today_enabled': False}

    cache_key = f'blaqvibes:today:v3:{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = {
        'today_enabled': True,
        'daily': None,
        'my_next_vibe': None,
        'unread_notifications': 0,
        'following_vibes': [],
        'discovery_vibes': [],
    }

    try:
        from gallery.daily import today_challenge
        data['daily'] = today_challenge()
    except Exception:
        pass

    try:
        from gallery.models import AppProject, Notification

        data['my_next_vibe'] = (
            AppProject.objects.filter(owner=user, status='published')
            .only('id', 'title', 'slug', 'stars', 'updated_at')
            .order_by('-updated_at')
            .first()
        )

        data['unread_notifications'] = Notification.objects.filter(
            user=user, is_read=False
        ).count()
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
                .order_by('-created_at')[:3]
            )
    except Exception:
        pass

    try:
        from gallery.models import AppProject
        data['discovery_vibes'] = list(
            AppProject.objects.filter(status='published')
            .exclude(owner=user)
            .select_related('owner')
            .only('id', 'title', 'slug', 'stars', 'created_at', 'owner__username')
            .order_by('-created_at')[:5]
        )
    except Exception:
        pass

    cache.set(cache_key, data, 30)
    return data
