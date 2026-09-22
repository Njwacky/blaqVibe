def extras(request):
    """
    Context processor — runs on EVERY request, so it must be fast.
    Heavy aggregations are cached (60-600s). User-specific counts use short cache
    or remain uncached but are single indexed queries.
    """
    from django.core.cache import cache

    unread = 0
    open_reports = 0
    open_appeals = 0
    attention = {'open': 0, 'critical': 0, 'awaiting_delete': 0, 'oldest_days': 0,
                 'next_deadline': None, 'due_reminder': False, 'reminder_seconds': 1800}
    quarantine_active = False
    quarantine_ends_at = None
    quarantine_reason = ''
    quarantine_appeal_open = False
    feedback_unread = 0
    feedback_fab_visible = True
    feedback_fab_tip_visible = True
    try:
        if request.COOKIES.get('blaq_fab_tip_dismissed') == '1':
            feedback_fab_tip_visible = False
    except Exception:
        pass

    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        # Unread notifications — cache 30s per user to avoid COUNT on every page
        try:
            cache_key = f"ctx:unread:{user.pk}"
            cached_unread = cache.get(cache_key)
            if cached_unread is not None:
                unread = cached_unread
            else:
                unread = request.user.notifications.filter(is_read=False).count()
                try:
                    cache.set(cache_key, unread, 30)
                except Exception:
                    pass
        except Exception:
            unread = 0

        try:
            from .attention import summary as attention_summary
            # Attention summary is already a single aggregate, but cache 60s
            cache_key = f"ctx:attention:{user.pk}"
            cached_att = cache.get(cache_key)
            if cached_att is not None:
                attention = cached_att
            else:
                attention = attention_summary(user)
                try:
                    cache.set(cache_key, attention, 60)
                except Exception:
                    pass
        except Exception:
            attention = {'open': 0, 'critical': 0, 'awaiting_delete': 0,
                         'oldest_days': 0, 'next_deadline': None, 'due_reminder': False,
                         'reminder_seconds': 1800}

        # Quarantine check — must be fresh, but cheap (indexed, single row)
        try:
            from users.quarantine import active_quarantine, open_appeal
            quarantine = active_quarantine(user)
            if quarantine is not None:
                quarantine_active = True
                quarantine_ends_at = quarantine.ends_at
                quarantine_reason = quarantine.reason_label
                quarantine_appeal_open = open_appeal(quarantine) is not None
        except Exception:
            quarantine_active = False

        try:
            feedback_fab_visible = bool(getattr(user.profile, 'show_feedback_fab', True))
        except Exception:
            feedback_fab_visible = True
        try:
            if getattr(user.profile, 'feedback_fab_tip_dismissed', False):
                feedback_fab_tip_visible = False
        except Exception:
            pass

        # Staff counts — cache 60s, not per-request COUNT
        try:
            if user.profile.is_moderator():
                cache_key = "ctx:mod_counts"
                cached = cache.get(cache_key)
                if cached:
                    open_reports, open_appeals = cached
                else:
                    from .models import AppReport
                    from users.models import QuarantineAppeal
                    open_reports = AppReport.objects.filter(status='open').count()
                    open_appeals = QuarantineAppeal.objects.filter(status='open').count()
                    try:
                        cache.set(cache_key, (open_reports, open_appeals), 60)
                    except Exception:
                        pass
        except Exception:
            open_reports = 0
            open_appeals = 0

        try:
            if user.profile.is_superadmin():
                cache_key = "ctx:feedback_unread"
                cached_fb = cache.get(cache_key)
                if cached_fb is not None:
                    feedback_unread = cached_fb
                else:
                    from django.db.models import F, Q
                    from users.models import FeedbackThread
                    feedback_unread = FeedbackThread.objects.filter(
                        Q(last_user_message_at__isnull=False)
                        & (Q(admin_last_read_at__isnull=True)
                           | Q(last_user_message_at__gt=F('admin_last_read_at'))),
                    ).count()
                    try:
                        cache.set(cache_key, feedback_unread, 60)
                    except Exception:
                        pass
        except Exception:
            feedback_unread = 0

    # Cached global values — same for all users, rarely change
    try:
        from .performance import (
            get_cached_footer_contacts,
            get_cached_social_providers,
            get_cached_site_settings,
        )
        footer_contacts = get_cached_footer_contacts()
        if not footer_contacts:
            # Fallback if cache miss and module fails
            from types import SimpleNamespace
            from users.footer_contacts import contact_as_dict
            footer_contacts = [
                contact_as_dict(SimpleNamespace(
                    kind='email', value='admin@blaqvibes.co.za', label='')),
                contact_as_dict(SimpleNamespace(
                    kind='github', value='Njwacky', label='')),
            ]
    except Exception:
        footer_contacts = []

    try:
        from .performance import get_cached_social_providers
        social_providers = get_cached_social_providers()
    except Exception:
        social_providers = []
        try:
            from users.social import configured_social_providers
            social_providers = configured_social_providers()
        except Exception:
            social_providers = []

    paystack_enabled = False
    try:
        from gallery.payments import paystack_enabled as _ps
        # Cache paystack check — it's env var read, but avoid repeated import overhead
        cache_key = "ctx:paystack_enabled"
        cached_ps = cache.get(cache_key)
        if cached_ps is not None:
            paystack_enabled = cached_ps
        else:
            paystack_enabled = _ps()
            try:
                cache.set(cache_key, paystack_enabled, 600)
            except Exception:
                pass
    except Exception:
        paystack_enabled = False

    nolo_backend = 'heuristic'
    try:
        cache_key = "ctx:nolo_backend"
        cached_nolo = cache.get(cache_key)
        if cached_nolo is not None:
            nolo_backend = cached_nolo
        else:
            from gallery.nolo_ai import configured_ai_backend
            nolo_backend = configured_ai_backend()
            try:
                cache.set(cache_key, nolo_backend, 600)
            except Exception:
                pass
    except Exception:
        nolo_backend = 'heuristic'

    pwa_enabled = True
    local_dev = False
    preview = False
    try:
        from django.conf import settings
        local_dev = bool(getattr(settings, 'LOCAL_DEV', False) or getattr(settings, 'DEBUG', False))
        preview = bool(getattr(settings, 'PREVIEW', False))
        # SiteSettings cached
        from .performance import get_cached_site_settings
        site = get_cached_site_settings()
        if site:
            pwa_enabled = site.pwa_enabled
        else:
            from users.models import SiteSettings
            pwa_enabled = SiteSettings.get().pwa_enabled
    except Exception:
        pass

    return {
        'unread_notifications': unread,
        'attention': attention,
        'open_reports': open_reports,
        'open_appeals': open_appeals,
        'feedback_unread': feedback_unread,
        'feedback_fab_visible': feedback_fab_visible,
        'feedback_fab_tip_visible': feedback_fab_tip_visible,
        'quarantine_active': quarantine_active,
        'quarantine_ends_at': quarantine_ends_at,
        'quarantine_reason': quarantine_reason,
        'quarantine_appeal_open': quarantine_appeal_open,
        'social_providers': social_providers,
        'paystack_enabled': paystack_enabled,
        'nolo_backend': nolo_backend,
        'pwa_enabled': pwa_enabled,
        'footer_contacts': footer_contacts,
        'local_dev': local_dev,
        'show_preview_hosting_notice': local_dev or preview,
    }
