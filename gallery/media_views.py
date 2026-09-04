from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404

BLOCKED_PREFIXES = ('apps/zips/', 'apps/versions/')

def serve_public_media(request, path):
    """Local MEDIA server that never streams paid ZIP bytes."""
    norm = (path or '').replace('\\', '/').lstrip('/')
    if not norm or '..' in norm.split('/'):
        raise Http404
    if any(norm.startswith(prefix) for prefix in BLOCKED_PREFIXES):
        raise Http404
    root = Path(settings.MEDIA_ROOT).resolve()
    full = (root / norm).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        raise Http404
    if not full.is_file():
        raise Http404
    return FileResponse(open(full, 'rb'))
