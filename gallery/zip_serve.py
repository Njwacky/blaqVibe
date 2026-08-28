"""Serve paid ZIPs only through gated views — never via /media/ or .url."""
from django.http import FileResponse, Http404, HttpResponseRedirect

from .storages import get_presigned_url, is_s3_enabled


def serve_named_zip(file_field, download_name):
    if not file_field:
        raise Http404
    if is_s3_enabled():
        url = get_presigned_url(file_field.name, expires=300, filename=download_name)
        if url:
            return HttpResponseRedirect(url)
    try:
        resp = FileResponse(
            file_field.open('rb'),
            as_attachment=True,
            filename=download_name,
            content_type='application/zip',
        )
        resp['X-Content-Type-Options'] = 'nosniff'
        resp['Cache-Control'] = 'no-store'
        return resp
    except FileNotFoundError:
        raise Http404


def serve_project_zip(project, user=None, ip=''):
    if not project.zip_file:
        raise Http404
    # One served ZIP = one clone, logged append-only for the admin charts
    # (source='zip') and counted on the project in the same code path.
    from .git_daemon import record_clone
    record_clone(project, user, 'zip', ip)
    return serve_named_zip(project.zip_file, f'{project.slug}.zip')


def owner_scan_reason(project) -> str:
    report = project.scan_report or {}
    if project.status == 'quarantined':
        return 'Virus or blocked secret found. Edit and re-upload a clean ZIP.'
    if report.get('clamav') == 'unavailable':
        return 'Virus scanner is offline. This vibe was not auto-published — a human will review it.'
    if report.get('secrets'):
        return 'Possible secrets detected in the ZIP. A moderator will review it before it goes live.'
    if project.status == 'pending':
        return 'Queued for review. You will get an in-app notification when it is live.'
    return ''


def _human_bytes(num) -> str:
    """Bytes → human string (e.g. 1536 → '1.5 KB'). No external deps."""
    try:
        num = float(num or 0)
    except (TypeError, ValueError):
        return '0 B'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(num) < 1024.0 or unit == 'TB':
            if unit == 'B':
                return f'{int(num)} {unit}'
            return f'{num:.1f} {unit}'
        num /= 1024.0
    return f'{num:.1f} TB'


def upload_file_info(project) -> dict:
    """Name + size of the artifact the creator uploaded, for the waiting UI.

    5 Whys: Why compute this instead of storing it? The FileField already
    knows the name and the storage backend knows the size; asking them keeps
    one source of truth and works identically on local disk and S3/R2. It is
    only ever shown to the owner/moderator while a vibe waits, so a single
    extra storage stat call on that page is cheap.
    """
    import os
    info = {
        'name': '',
        'size_bytes': 0,
        'size_human': '',
        'file_count': int(getattr(project, 'file_count', 0) or 0),
        'is_zip': False,
        'is_snippet': False,
    }
    zf = getattr(project, 'zip_file', None)
    if zf:
        info['is_zip'] = True
        try:
            info['name'] = os.path.basename(zf.name) or f'{project.slug}.zip'
        except Exception:
            info['name'] = f'{project.slug}.zip'
        try:
            info['size_bytes'] = int(zf.size or 0)
        except Exception:
            info['size_bytes'] = 0
        info['size_human'] = _human_bytes(info['size_bytes'])
    else:
        # Snippet upload — measure the pasted code so the creator still sees
        # "what did I just send" while it is checked.
        info['is_snippet'] = True
        try:
            total = sum(len((getattr(project, f, '') or '').encode('utf-8'))
                        for f in ('html_code', 'css_code', 'js_code'))
        except Exception:
            total = 0
        info['name'] = f'{project.slug} (snippet: HTML/CSS/JS)'
        info['size_bytes'] = total
        info['size_human'] = _human_bytes(total)
        if not info['file_count']:
            info['file_count'] = sum(
                1 for f in ('html_code', 'css_code', 'js_code')
                if (getattr(project, f, '') or '').strip()
            )
    return info


def scan_progress(project) -> dict:
    """A rich, owner-facing description of *why* a vibe is still waiting.

    The old waiting banner showed one bare status word ("pending") and left
    the creator staring at a stalled page with no idea what was happening.
    This returns an ordered checklist of the exact pipeline stages plus the
    uploaded file's name/size/count so the page can explain itself.

    Stage states: 'done' | 'active' | 'pending' | 'blocked'. Nothing here is
    a secret — filenames of flagged files live in scan_report and are only
    surfaced to the owner/moderator via owner_scan_reason.
    """
    report = project.scan_report or {}
    job = getattr(project, 'scan_job', None)
    job_status = getattr(job, 'status', None) if job else None
    status = project.status
    is_snippet = not bool(getattr(project, 'zip_file', None))

    quarantined = status == 'quarantined'
    scanner_off = report.get('clamav') == 'unavailable'
    has_secrets = bool(report.get('secrets'))
    published = status == 'published'
    held = (not published) and (quarantined or scanner_off or has_secrets)

    def state_for(done, active=False, blocked=False):
        if blocked:
            return 'blocked'
        if done:
            return 'done'
        if active:
            return 'active'
        return 'pending'

    # Uploaded — always done once the row exists.
    steps = [{
        'key': 'uploaded',
        'label': 'Uploaded',
        'detail': 'We received your files.',
        'state': 'done',
    }]

    if is_snippet:
        # Snippets skip the queue entirely (see publish view): a fast regex
        # secrets sweep, then either auto-publish or hold for review.
        steps.append({
            'key': 'checks',
            'label': 'Safety checks',
            'detail': ('Possible secrets found — held for a moderator.' if has_secrets
                       else 'A virus was found — blocked.' if quarantined
                       else 'Scanning the pasted code for leaked secrets.'),
            'state': state_for(not held, active=False, blocked=held),
        })
    else:
        # Virus scan
        virus_done = quarantined or has_secrets or published or scanner_off
        steps.append({
            'key': 'virus',
            'label': 'Virus scan',
            'detail': ('A virus was found — blocked.' if quarantined
                       else 'Scanner offline — held for a human.' if scanner_off
                       else 'Checking the ZIP with ClamAV.'),
            'state': state_for(virus_done and not (quarantined or scanner_off),
                               active=not virus_done,
                               blocked=quarantined or scanner_off),
        })
        # Secret scan
        secret_done = published or has_secrets or quarantined
        steps.append({
            'key': 'secrets',
            'label': 'Secret scan',
            'detail': ('Possible secrets found — held for a moderator.' if has_secrets
                       else 'Looking for API keys or passwords in the files.'),
            'state': ('pending' if quarantined or scanner_off
                      else state_for(published, active=not secret_done,
                                     blocked=has_secrets)),
        })

    # Publish
    steps.append({
        'key': 'publish',
        'label': 'Go live',
        'detail': ('Your vibe is live!' if published
                   else 'A moderator will review, then it goes live.' if held
                   else 'Publishes automatically once checks pass.'),
        'state': state_for(published, active=(not published and not held),
                           blocked=False),
    })

    # A checklist reads clearest with a single "in progress" step: keep the
    # FIRST active/pending step active and demote any later actives to
    # pending, so progress feels sequential instead of "everything at once".
    # (Blocked and done states are never touched.)
    seen_active = False
    for step in steps:
        if step['state'] == 'active':
            if seen_active:
                step['state'] = 'pending'
            else:
                seen_active = True

    # Where in the queue? Only meaningful while genuinely waiting.
    queue_position = 0
    if not published and not held:
        try:
            from .models import ScanJob
            if job and job_status in ('queued', 'scanning'):
                queue_position = ScanJob.objects.filter(
                    status__in=('queued', 'scanning'),
                    created_at__lte=job.created_at,
                ).count()
        except Exception:
            queue_position = 0

    if published:
        headline = 'Your vibe is live!'
    elif quarantined:
        headline = 'Blocked — a virus or secret was found'
    elif has_secrets:
        headline = 'Held for review — possible secrets in the ZIP'
    elif scanner_off:
        headline = 'Held for a human — the scanner is offline'
    else:
        headline = 'Checking your vibe — almost there'

    return {
        'status': job_status or status,
        'headline': headline,
        'reason': owner_scan_reason(project),
        'steps': steps,
        'held': held,
        'published': published,
        'quarantined': quarantined,
        'queue_position': queue_position,
        'file': upload_file_info(project),
    }
