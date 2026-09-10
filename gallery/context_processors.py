def extras(request):
    unread = 0
    open_reports = 0
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        try:
            unread = request.user.notifications.filter(is_read=False).count()
        except Exception:
            unread = 0
        # One count only for staff, so the nav badge is free to show. Reads
        # an indexed row set; never performed on a public cache-key path.
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
    # Site-level values that every template needs. The public footer contacts
    # are rows an operator maintains (add, reorder, hide) without changing
    # templates or deploying. The fallback list covers a deployment that has
    # not run its migration yet, so the footer is never empty by accident.
    pwa_enabled = True
    local_dev = False
    preview = False
    footer_contacts = None
    try:
        from django.conf import settings
        local_dev = bool(getattr(settings, 'LOCAL_DEV', False) or getattr(settings, 'DEBUG', False))
        preview = bool(getattr(settings, 'PREVIEW', False))
        from users.models import SiteSettings
        pwa_enabled = SiteSettings.get().pwa_enabled
    except Exception:
        pass
    try:
        from users.footer_contacts import public_footer_contacts
        footer_contacts = public_footer_contacts()
    except Exception:
        footer_contacts = None
    if footer_contacts is None:
        footer_contacts = [
            {'kind': 'email', 'icon': '✉️', 'label': 'admin@blaqvibes.co.za',
             'href': 'mailto:admin@blaqvibes.co.za', 'external': False},
            {'kind': 'github', 'icon': '🐙', 'label': 'GitHub @Njwacky',
             'href': 'https://github.com/Njwacky', 'external': True},
        ]
    return {
        'unread_notifications': unread,
        'open_reports': open_reports,
        'social_providers': social_providers,
        'paystack_enabled': paystack_enabled,
        'nolo_backend': nolo_backend,
        'pwa_enabled': pwa_enabled,
        'footer_contacts': footer_contacts,
        'local_dev': local_dev,
        # The hosting disclaimer is useful on local/Arena previews, but it is
        # intentionally never rendered by a production configuration.
        'show_preview_hosting_notice': local_dev or preview,
    }
