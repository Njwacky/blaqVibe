def extras(request):
    unread = 0
    if getattr(request, 'user', None) and request.user.is_authenticated:
        try:
            unread = request.user.notifications.filter(is_read=False).count()
        except Exception:
            unread = 0
    return {'unread_notifications': unread}
