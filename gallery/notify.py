import logging

logger = logging.getLogger(__name__)


def notify(user, kind, title, body='', url=''):
    if not user:
        return None
    try:
        from .models import Notification
        return Notification.objects.create(
            user=user,
            kind=kind,
            title=title[:200],
            body=(body or '')[:400],
            url=(url or '')[:300],
        )
    except Exception:
        logger.exception('notify failed kind=%s', kind)
        return None
