from datetime import timedelta

from django import template
from django.db.models import Sum
from django.utils import timezone

register = template.Library()

@register.inclusion_tag('gallery/includes/today_loop.html', takes_context=True)
def today_loop(context):
    """Render the logged-in creator's daily return loop.

    This is deliberately read-only and composes existing BlaqVibes signals:
    today's challenge, recent social activity, followed creators, the
    creator's own vibes, weekly XP, and rank. Keeping it in a template tag
    means the existing feed view and its filters stay untouched.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'today_enabled': False}

    data = {'today_enabled': True}
    try:
        from gallery.daily import today_challenge
        data['daily'] = today_challenge()
    except Exception:
        data['daily'] = None

    try:
        from users.models import XPEvent
        since = timezone.now() - timedelta(days=7)
        data['weekly_xp'] = XPEvent.objects.filter(
            user=user, created_at__gte=since
        ).aggregate(total=Sum('amount'))['total'] or 0
    except Exception:
        data['weekly_xp'] = 0

    try:
        from gallery.ranks import contributor_bonus
        data['rank'] = contributor_bonus(user)
    except Exception:
        data['rank'] = {'name': 'Bronze', 'threshold': 0}

    try:
        from gallery.models import AppProject
        data['my_vibes'] = list(
            AppProject.objects.filter(owner=user, status='published')
            .order_by('-updated_at')[:3]
        )
        data['my_next_vibe'] = data['my_vibes'][0] if data['my_vibes'] else None
    except Exception:
        data['my_vibes'] = []
        data['my_next_vibe'] = None

    try:
        from gallery.models import Notification
        data['unread_notifications'] = Notification.objects.filter(
            user=user, is_read=False
        ).count()
        data['recent_notifications'] = list(
            Notification.objects.filter(user=user)
            .order_by('-created_at')[:3]
        )
    except Exception:
        data['unread_notifications'] = 0
        data['recent_notifications'] = []

    try:
        from users.models import Follow
        followed_ids = Follow.objects.filter(follower=user).values_list(
            'following_id', flat=True
        )[:50]
        from gallery.models import AppProject
        data['following_vibes'] = list(
            AppProject.objects.filter(
                owner_id__in=list(followed_ids), status='published'
            )
            .select_related('owner', 'owner__profile')
            .order_by('-created_at')[:4]
        )
    except Exception:
        data['following_vibes'] = []

    try:
        # A lightweight social proof signal: stars, forks and reviews on the
        # creator's published work during the last 7 days. No private content
        # is exposed; the rows are limited to projects owned by this user.
        from gallery.models import AppProject, Comment, Review, Star
        since = timezone.now() - timedelta(days=7)
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
        data['week_stars'] = data['week_comments'] = data['week_reviews'] = 0

    return data
