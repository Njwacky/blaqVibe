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

    # The loop renders above the feed grid, so a vibe already on the grid
    # must never appear here too — on a small catalog that used to list the
    # same uploads twice ("why is every app on this page two times?").
    # Pages without a grid (the tag is feed-only today) keep the old shape.
    page = context.get('page')
    grid_ids = {p.id for p in getattr(page, 'object_list', None) or []}

    cache_key = f'blaqvibes:today:v5:{user.pk}'
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

        my_next = (
            AppProject.objects.filter(owner=user, status='published')
            .only('id', 'title', 'slug', 'stars', 'updated_at')
            .order_by('-updated_at')
            .first()
        )
        # "Your latest" is a nudge, not a second card: when the grid below
        # already shows that vibe, keep the page free of the repeat.
        if my_next is not None and my_next.id not in grid_ids:
            data['my_next_vibe'] = my_next

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
            followed_qs = (
                AppProject.objects.filter(
                    owner_id__in=followed_ids, status='published'
                )
                .select_related('owner')
                .only('id', 'title', 'slug', 'stars', 'created_at', 'owner__username')
                .order_by('-created_at')
            )
            if grid_ids:
                followed_qs = followed_qs.exclude(id__in=grid_ids)
            data['following_vibes'] = list(followed_qs[:3])
    except Exception:
        pass

    try:
        from gallery.models import AppProject
        discovery_qs = (
            AppProject.objects.filter(status='published')
            .exclude(owner=user)
            .select_related('owner')
            .only('id', 'title', 'slug', 'stars', 'created_at', 'owner__username')
            .order_by('-created_at')
        )
        if grid_ids:
            discovery_qs = discovery_qs.exclude(id__in=grid_ids)
        data['discovery_vibes'] = list(discovery_qs[:5])
        data['next_remix'] = data['discovery_vibes'][0] if data['discovery_vibes'] else None
        next_review_qs = (
            AppProject.objects.filter(status='published', review_count=0)
            .exclude(owner=user)
            .select_related('owner')
            .only('id', 'title', 'slug', 'stars', 'created_at', 'owner__username')
            .order_by('-created_at')
        )
        if grid_ids:
            next_review_qs = next_review_qs.exclude(id__in=grid_ids)
        data['next_review'] = next_review_qs.first()
    except Exception:
        pass

    cache.set(cache_key, data, 30)
    return data
