"""What Nolo chat is allowed to know about live BlaqVibes data.

Nolo chat has no database access of its own. The only dynamic context it
receives is built here, and it is deliberately the same information the
public chat page already shows to a logged-out visitor: the published-vibe
count (so "how many vibes are published?" gets the real number), the recent
*published* vibes (title, public URL, category) and the category list.

Never add to this module anything a logged-out visitor cannot see on the
site — no owners' emails, no pending/quarantined/removed projects, no
balances, trades, sales or notifications. `test_nolo_scope.py` pins that.
"""
import logging

from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

PUBLIC_PAGES = (
    ('feed', '/'),
    ('discover', '/discover/'),
    ('publish (ZIP or snippet)', '/publish/'),
    ('Studio (HTML/CSS/JS in the browser)', '/studio/'),
    ('starter templates', '/start/'),
    ('challenges', '/challenges/'),
    ('battles', '/battle/'),
    ('prompt skills', '/skills/'),
    ('launch guides', '/launch/'),
    ('trust legend', '/trust/'),
    ('Nolo chat', '/nolo/chat/'),
)


def _clean(value, limit):
    from .ai_safety import redact_for_ai
    text = ' '.join(str(value or '').split())
    return redact_for_ai(text, limit)


def public_chat_context(limit=8, max_chars=900):
    """Compact, public-only context for a Nolo chat turn ('' on any error).

    Only ``status='published'`` projects are ever listed — the same filter
    the chat page uses for its "latest vibes" strip. The stats line comes
    first so a data question ("how many vibes are published?") keeps its
    answer even when truncation cuts the tail.
    """
    try:
        from .models import AppProject, Category
        published = AppProject.objects.filter(status='published')
        total_published = published.count()
        week_ago = timezone.now() - timedelta(days=7)
        fresh_published = published.filter(created_at__gte=week_ago).count()
        stats = f'Published vibes: {total_published} total, {fresh_published} in the last 7 days.'
        recent = (
            published.select_related('category')
            .order_by('-created_at')[: max(1, int(limit))]
        )
        lines = []
        for project in recent:
            category = getattr(getattr(project, 'category', None), 'name', '') or ''
            title = _clean(project.title, 60)
            if not title or not project.slug:
                continue
            suffix = f' ({_clean(category, 30)})' if category else ''
            lines.append(f'- {title} — /app/{project.slug}/{suffix}')
        categories = [_clean(c.name, 30) for c in Category.objects.all().order_by('order')[:12]]
    except Exception as exc:
        # No context is a safe degradation: Nolo answers from the system facts.
        logger.warning('nolo public context unavailable: %s', exc)
        return ''

    parts = [stats]
    if lines:
        parts.append('Latest published vibes:\n' + '\n'.join(lines))
    if categories:
        parts.append('Categories: ' + ', '.join(c for c in categories if c))
    text = '\n'.join(parts).strip()
    return text[: max(120, int(max_chars))]
