"""Server-rendered Open Graph share card for project pages (§14).

When somebody shares a project outside BlaqVibes, the preview must carry
the story: PROJECT NAME, CREATOR, WHAT IT DOES, BUILD METHOD, STARS,
REMIXES. If the project has no thumbnail, this view renders the card
itself — the platform markets itself every time a project travels.

Backend only: pure PIL, no user-controlled markup reaches the canvas —
only sanitized model fields, drawn as plain text.
"""
import html
import io
import logging
import re

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import cache_control

logger = logging.getLogger(__name__)

W, H = 1200, 630
BG = (10, 10, 15)
CARD = (20, 20, 30)
ACCENT = (124, 58, 237)
ACCENT_SOFT = (76, 29, 149)
TEXT = (244, 244, 248)
MUTED = (160, 160, 175)
SUCCESS = (16, 185, 129)
WARNING = (245, 158, 11)

PAD = 56
CACHE_TTL = 3600


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _glyph_safe(text):
    """The raster font only covers Latin-1; map common extras, drop the rest
    so shared previews never show tofu boxes."""
    out = []
    for ch in text:
        cp = ord(ch)
        if cp <= 0xFF:
            out.append(ch)
        elif ch in '★☆':
            out.append('*')
        elif ch in '→➜➔':
            out.append('->')
        elif ch == '⑂':
            out.append('remix ')
        elif ch == '…':
            out.append('...')
        elif ch in '·•':
            out.append('-')
        else:
            out.append(' ')
    return ''.join(out)


def _clean(value, limit):
    text = html.unescape(str(value or '')).strip()
    text = ' '.join(_glyph_safe(text).split())
    text = re.sub(r'(\d+) \*', r'\1 stars', text)
    return text[:limit]


def _wrap(draw, text, font, max_width, max_lines):
    """Greedy word wrap capped at max_lines — overflow ends in an ellipsis."""
    words = text.split()
    lines, line, overflow = [], '', False
    for word in words:
        trial = f'{line} {word}'.strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
            continue
        if not line:
            # One word wider than the whole box: hard-cut it.
            while len(word) > 1 and draw.textlength(word + '...', font=font) > max_width:
                word = word[:-1]
            line = word + '...'
            overflow = True
            break
        lines.append(line)
        line = word
        if len(lines) == max_lines:
            line = ''
            overflow = True
            break
    if line and len(lines) < max_lines:
        lines.append(line)
        line = ''
    if overflow and lines:
        lines[-1] = lines[-1].rstrip() + '...'
    return lines


def _chip(draw, xy, label, color):
    font = _font(22)
    pad_x, pad_y = 14, 8
    width = draw.textlength(label, font=font) + pad_x * 2
    x, y = xy
    draw.rounded_rectangle([x, y, x + width, y + 40], radius=20, outline=color, width=2)
    draw.text((x + pad_x, y + pad_y - 1), label, font=font, fill=color)
    return width


def render_share_card(project, forks_count):
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Subtle brand gradient corner — pure geometry, deterministic.
    for i in range(0, 320, 2):
        alpha = max(0, 320 - i) / 320
        tint = tuple(int(c * alpha * 0.22) for c in ACCENT)
        draw.line([(W - i, 0), (W, i)], fill=(BG[0] + tint[0], BG[1] + tint[1], BG[2] + tint[2]))

    # Header: wordmark + loop.
    draw.text((PAD, 40), 'BlaqVibes', font=_font(34), fill=TEXT)
    draw.text((PAD + 185, 52), 'BUILD · SHOW · REMIX · COMPETE', font=_font(20), fill=MUTED)

    # Thumbnail (when real) rides on the right; text gets the rest.
    text_width = W - PAD * 2
    thumb = getattr(project, 'thumbnail', None)
    try:
        if thumb:
            thumb.im  # force open
            timg = Image.open(thumb).convert('RGB')
            timg.thumbnail((300, 300))
            img.paste(timg, (W - PAD - timg.width, 130))
            draw.rectangle(
                [W - PAD - timg.width - 2, 128, W - PAD + 2, 130 + timg.height + 2],
                outline=ACCENT_SOFT, width=2,
            )
            text_width = W - PAD * 2 - timg.width - 40
    except Exception:
        logger.exception('share card thumbnail failed %s', getattr(project, 'slug', '?'))

    # Title + what it does.
    title_font = _font(52)
    title_lines = _wrap(draw, _clean(project.title, 120), title_font, text_width, 2)
    y = 130
    for line in title_lines:
        draw.text((PAD, y), line, font=title_font, fill=TEXT)
        y += 62
    y += 6
    desc_lines = _wrap(draw, _clean(project.short_description, 220), _font(26), text_width, 2)
    for line in desc_lines:
        draw.text((PAD, y), line, font=_font(26), fill=MUTED)
        y += 34

    # Build method + safety chips (evidence, not decoration).
    y = max(y + 16, 330)
    method_colors = {
        'human_built': SUCCESS,
        'ai_assisted': WARNING,
        'ai_generated': WARNING,
        'remixed': ACCENT,
    }
    x = PAD
    try:
        label = project.build_method_label.upper()
        note = f' · {project.ai_tool}' if (project.ai_tool and project.build_method in ('ai_assisted', 'ai_generated')) else ''
        x += _chip(draw, (x, y), f'BUILD: {label}{note}', method_colors.get(project.build_method, MUTED)) + 14
        if getattr(project, 'trust', 'unknown') == 'verified':
            x += _chip(draw, (x, y), 'SAFETY CHECKED', SUCCESS) + 14
        elif getattr(project, 'trust', 'unknown') == 'scanned':
            x += _chip(draw, (x, y), 'SCANNED', MUTED) + 14
    except Exception:
        logger.exception('share card chips failed %s', getattr(project, 'slug', '?'))

    # Footer story: creator, stars, remixes, CTA.
    draw.line([(PAD, H - 120), (W - PAD, H - 120)], fill=(45, 45, 60), width=1)
    creator = _clean(getattr(project.owner, 'username', 'builder'), 40)
    draw.text((PAD, H - 92), f'by @{creator}', font=_font(28), fill=TEXT)
    stars = getattr(project, 'stars', 0)
    draw.text((PAD, H - 52), f'STARS {stars}   REMIXES {forks_count}', font=_font(24), fill=MUTED)
    cta = 'View Project on BlaqVibes'
    draw.text((W - PAD - draw.textlength(cta, font=_font(26)), H - 72), cta, font=_font(26), fill=ACCENT)

    buffer = io.BytesIO()
    img.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


@cache_control(public=True, max_age=CACHE_TTL)
def share_card(request, slug):
    """PNG share card for published projects. Same visibility rule as the
    detail page's public surface: unpublished slugs 404, never leak."""
    from .models import AppProject
    project = get_object_or_404(AppProject, slug=slug, status='published')
    try:
        forks_count = AppProject.objects.filter(forked_from_id=project.pk, status='published').count()
        png = render_share_card(project, forks_count)
    except Exception:
        logger.exception('share card render failed %s', slug)
        return HttpResponse(status=404)
    return HttpResponse(png, content_type='image/png')
