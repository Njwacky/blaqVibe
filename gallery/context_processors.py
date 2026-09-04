def extras(request):
    unread = 0
    open_reports = 0
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        try:
            unread = request.user.notifications.filter(is_read=False).count()
        except Exception:
            unread = 0
        try:
            if user.profile.is_moderator():
                from .models import AppReport
                open_reports = AppReport.objects.filter(status='open').count()
        except Exception:
            open_reports = 0
    social_providers = []
    try:
        from users.social import configured_social_providers
        social_providers = configured_social_providers()
    except Exception:
        social_providers = []
    paystack_enabled = False
    try:
        from gallery.payments import paystack_enabled as _ps
        paystack_enabled = _ps()
    except Exception:
        paystack_enabled = False
    nolo_backend = 'heuristic'
    try:
        from gallery.nolo_ai import configured_ai_backend
        nolo_backend = configured_ai_backend()
    except Exception:
        nolo_backend = 'heuristic'
    pwa_enabled = True
    try:
        from users.models import SiteSettings
        pwa_enabled = SiteSettings.get().pwa_enabled
    except Exception:
        pass
    local_dev = False
    try:
        from django.conf import settings
        local_dev = bool(getattr(settings, 'LOCAL_DEV', False) or getattr(settings, 'DEBUG', False))
    except Exception:
        local_dev = False
    return {
        'unread_notifications': unread,
        'open_reports': open_reports,
        'social_providers': social_providers,
        'paystack_enabled': paystack_enabled,
        'nolo_backend': nolo_backend,
        'pwa_enabled': pwa_enabled,
        'local_dev': local_dev,
    }
