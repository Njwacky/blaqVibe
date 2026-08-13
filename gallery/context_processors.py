def extras(request):
    unread = 0
    if getattr(request, 'user', None) and request.user.is_authenticated:
        try:
            unread = request.user.notifications.filter(is_read=False).count()
        except Exception:
            unread = 0
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
    return {
        'unread_notifications': unread,
        'social_providers': social_providers,
        'paystack_enabled': paystack_enabled,
        'nolo_backend': nolo_backend,
    }
