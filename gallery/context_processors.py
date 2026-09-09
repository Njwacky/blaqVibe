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
    # are stored with singleton SiteSettings so operators can update them
    # without changing templates or deploying. Keep the original public values
    # as a safe fallback while a new deployment is waiting for its migration.
    pwa_enabled = True
    footer_contact = {
        'email': 'admin@blaqvibes.co.za',
        'github_url': 'https://github.com/Njwacky',
        'github_label': 'GitHub @Njwacky',
    }
    local_dev = False
    preview = False
    try:
        from django.conf import settings
        local_dev = bool(getattr(settings, 'LOCAL_DEV', False) or getattr(settings, 'DEBUG', False))
        preview = bool(getattr(settings, 'PREVIEW', False))
        from users.models import SiteSettings
        site = SiteSettings.get()
        pwa_enabled = site.pwa_enabled
        footer_contact = {
            'email': site.footer_contact_email,
            'github_url': site.footer_github_url,
            'github_label': site.footer_github_label,
        }
    except Exception:
        pass
    return {
        'unread_notifications': unread,
        'open_reports': open_reports,
        'social_providers': social_providers,
        'paystack_enabled': paystack_enabled,
        'nolo_backend': nolo_backend,
        'pwa_enabled': pwa_enabled,
        'footer_contact': footer_contact,
        'local_dev': local_dev,
        # The hosting disclaimer is useful on local/Arena previews, but it is
        # intentionally never rendered by a production configuration.
        'show_preview_hosting_notice': local_dev or preview,
    }
