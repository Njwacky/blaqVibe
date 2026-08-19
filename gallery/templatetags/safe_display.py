"""Render-time language backstop for public templates.

Usage:
    {% load safe_display %}
    {{ project.title|public_text:"Untitled vibe" }}
    {{ project.readme_html|public_html|safe }}

Both filters delegate to gallery.profanity.display_text — the ONE
place the rule lives, shared with the public JSON API. A template that
forgets a field still cannot leak a blocked word: the filter re-checks
at render time and swaps the whole value for a placeholder.

Why two filters and not one? Plain fields (titles, bios, usernames)
escape as usual. Fields that already hold rendered HTML (body_html,
readme_html) must stay marked safe AFTER the check, or |safe would be
applied to raw user HTML. public_html only ever returns the placeholder
(our own constant, safe) or the stored, already-sanitized HTML.
"""
from django import template
from django.utils.safestring import mark_safe

from gallery.profanity import contains_profanity, display_text

register = template.Library()

# Shown where a hidden comment would have rendered — same wording the
# Comment.save() hide path uses, so a blocked comment looks identical
# whether the ORM or the template caught it.
_HIDDEN_HTML = (
    '<p>This comment was hidden because it used language '
    'that is not allowed here.</p>'
)


@register.filter(name='public_text')
def public_text(value, placeholder=''):
    """Blocked word → placeholder. Clean value passes through untouched."""
    return display_text(value, placeholder)


@register.filter(name='public_html')
def public_html(value, placeholder_html=None):
    """Same gate for fields rendered with |safe.

    The check runs on the HTML string itself — after folding, tags
    disappear and only the words remain, so a slur inside <b>...</b>
    is still caught.
    """
    if placeholder_html is None:
        placeholder_html = _HIDDEN_HTML
    text = '' if value is None else str(value)
    if text.strip() and contains_profanity(text):
        return mark_safe(placeholder_html)
    return value
