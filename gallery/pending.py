"""Owner-facing 'waiting for approval' state.

After an upload the vibe is pending: virus scan, secrets check, then
sometimes a human. The page used to show a one-line banner and keep
pulsing, so people thought it had frozen. This module is the single
source of truth for:

1. Why the page is sitting still (scan vs human hold vs quarantine).
2. The file receipt — name, bytes, file count — so the wait has an
   identity, not a blank spinner.
3. Whether the JS poll should keep spinning, and how fast.

Nothing here is a secret: filenames of leaked keys stay in scan_report
and never reach this dict. Templates and /scan-status/ both read it.
"""
from __future__ import annotations

import os
import re

from .zip_serve import owner_scan_reason

# Django's FileSystemStorage.get_available_name appends _XXXXXXX (7 chars)
# when a name already exists. Show the name the uploader picked.
_STORAGE_SUFFIX = re.compile(r'_[A-Za-z0-9]{7}(?=\.[^.]+$)')


def display_file_name(name: str) -> str:
    base = os.path.basename(name or '')
    return _STORAGE_SUFFIX.sub('', base)


def format_bytes(n) -> str:
    """Human size plus the raw byte count the user asked to see."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 0:
        n = 0
    raw = f'{n:,} bytes'
    if n < 1024:
        return raw
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB ({raw})'
    return f'{n / (1024 * 1024):.2f} MB ({raw})'


def upload_receipt(project) -> dict:
    """Name + bytes of what was just uploaded. ZIP or snippet."""
    zip_file = getattr(project, 'zip_file', None)
    if zip_file:
        name = display_file_name(getattr(zip_file, 'name', '') or '') or f'{project.slug}.zip'
        try:
            size = int(zip_file.size or 0)
        except Exception:
            size = 0
        return {
            'file_name': name,
            'file_bytes': size,
            'file_bytes_label': format_bytes(size),
            'file_count': int(getattr(project, 'file_count', 0) or 0),
            'kind': 'zip',
        }
    html = getattr(project, 'html_code', '') or ''
    css = getattr(project, 'css_code', '') or ''
    js = getattr(project, 'js_code', '') or ''
    size = len(html.encode('utf-8')) + len(css.encode('utf-8')) + len(js.encode('utf-8'))
    parts = sum(1 for chunk in (html, css, js) if chunk.strip())
    title = (getattr(project, 'title', '') or project.slug or 'snippet').strip()
    return {
        'file_name': f'{title}.html',
        'file_bytes': size,
        'file_bytes_label': format_bytes(size),
        'file_count': parts,
        'kind': 'snippet',
    }


def _job_status(project) -> str:
    job = getattr(project, 'scan_job', None)
    return getattr(job, 'status', '') or ''


def hold_state(project) -> dict:
    """Full waiting-room payload — safe for templates and JSON."""
    receipt = upload_receipt(project)
    status = getattr(project, 'status', '') or ''
    report = getattr(project, 'scan_report', None) or {}
    job = _job_status(project)
    reason = owner_scan_reason(project)

    if status == 'published':
        phase = 'published'
        headline = 'Your vibe is live'
        why = 'The scan finished clean and the listing is on the feed.'
        next_step = 'Share the page — people can now find it.'
        poll = False
        poll_ms = 0
        status_label = 'Live'
    elif status == 'removed':
        phase = 'removed'
        headline = 'This vibe was removed'
        why = 'The listing is gone. People who already traded or bought it keep their download.'
        next_step = 'Nothing else to wait for.'
        poll = False
        poll_ms = 0
        status_label = 'Removed'
    elif status == 'quarantined' or job == 'quarantined':
        phase = 'quarantined'
        headline = 'Quarantined — this vibe will not go live'
        why = 'The scanner blocked it. This is a hold, not a crash.'
        next_step = 'Edit and re-upload a clean ZIP. Inbox has the same note.'
        poll = False
        poll_ms = 0
        status_label = 'Quarantined'
    elif report.get('clamav') == 'unavailable':
        phase = 'human_review'
        headline = 'Waiting for approval — scanner is offline'
        why = (
            'This page is not stuck. The virus scanner is offline, so we did '
            'not auto-publish. A person has to sign off before it reaches the feed.'
        )
        next_step = 'You can close this tab. Inbox will ping you when a moderator decides.'
        poll = True
        poll_ms = 15000
        status_label = 'Waiting for a person'
    elif report.get('secrets'):
        phase = 'human_review'
        headline = 'Waiting for approval — possible secrets found'
        why = (
            'This page is not stuck. The ZIP looked like it might contain a leaked '
            'key, so a moderator has to look before it can go live.'
        )
        next_step = 'You can close this tab. Inbox will ping you when a moderator decides.'
        poll = True
        poll_ms = 15000
        status_label = 'Waiting for a person'
    elif job in ('queued', 'scanning'):
        phase = 'scanning'
        headline = 'Waiting for approval — scan in progress'
        why = (
            'This page is not stuck. After an upload we hold the vibe off the '
            'public feed while we scan it (virus → secrets → dependencies). '
            'The listing goes live only if that chain is clean.'
        )
        next_step = 'Keep this tab open and we will refresh it, or close it — Inbox will ping you either way.'
        poll = True
        poll_ms = 2000
        status_label = 'Scanning'
    elif not getattr(project, 'zip_file', None):
        phase = 'human_review'
        headline = 'Waiting for approval — first snippets need a human'
        why = (
            'This page is not stuck. New creators’ snippets wait for a moderator '
            '(after three live vibes, snippets publish on their own). The hold is '
            'the product, not a hang.'
        )
        next_step = 'You can close this tab. Inbox will ping you when it is approved.'
        poll = True
        poll_ms = 15000
        status_label = 'Waiting for a person'
    else:
        phase = 'human_review'
        headline = 'Waiting for approval — a person still has to say yes'
        why = (
            'This page is not stuck. The automatic scan could not publish this '
            'vibe, so it is in the moderation queue until someone approves it.'
        )
        next_step = 'You can close this tab. Inbox will ping you when a moderator decides.'
        poll = True
        poll_ms = 8000
        status_label = 'Waiting for a person'

    steps = _pipeline_steps(phase, job, report, status)
    return {
        'phase': phase,
        'headline': headline,
        'why_waiting': why,
        'next_step': next_step,
        'reason': reason,
        'poll': poll,
        'poll_ms': poll_ms,
        'status_label': status_label,
        'receipt': receipt,
        'steps': steps,
    }


def _pipeline_steps(phase, job, report, status) -> list:
    def mark(done=False, active=False, hold=False, skip=False):
        if skip:
            return 'skip'
        if hold:
            return 'hold'
        if done:
            return 'done'
        if active:
            return 'active'
        return 'todo'

    published = status == 'published' or phase == 'published'
    quarantined = phase == 'quarantined'
    scanning = phase == 'scanning'
    human = phase == 'human_review'
    clamav = report.get('clamav')
    secrets = bool(report.get('secrets'))
    virus_done = published or quarantined or human or clamav in ('clean', 'unavailable', 'disabled')
    virus_hold = clamav == 'unavailable' or quarantined
    secrets_done = published or quarantined or human or secrets or clamav == 'clean'
    review_active = human
    review_done = published
    return [
        {'id': 'upload', 'label': 'Upload received', 'state': mark(done=True)},
        {
            'id': 'virus',
            'label': 'Virus scan',
            'state': mark(
                done=virus_done and not (scanning and job in ('queued', 'scanning') and not clamav),
                active=scanning and not clamav,
                hold=virus_hold,
            ),
        },
        {
            'id': 'secrets',
            'label': 'Secrets check',
            'state': mark(
                done=secrets_done and not scanning,
                active=scanning and bool(clamav) and not secrets,
                hold=secrets,
            ),
        },
        {
            'id': 'review',
            'label': 'Approval',
            'state': mark(done=review_done, active=review_active, hold=review_active),
        },
        {
            'id': 'live',
            'label': 'Live on the feed',
            'state': mark(done=published, skip=quarantined),
        },
    ]


def notify_queued(project) -> None:
    """Inbox note at the moment the vibe enters the wait — not only at the end.

    5 Whys:
    1. Why notify on queue, not only on publish? The toast dies with the
       tab; people left the waiting page thinking nothing happened.
    2. Why include the file name and bytes? A blank "queued" row is the
       same silence as no row. The receipt proves we got *this* upload.
    3. Why not email here? Scan/publish already emails; a second email
       per upload is noise. Inbox is the durable in-app channel.
    4. Why kind='queued'? Distinct from 'published' / 'quarantined' so
       the inbox is a timeline, not three identical titles.
    5. Why crush-silently via notify()? A broken inbox must never fail
       the upload that just landed.
    """
    from .notify import notify

    owner = getattr(project, 'owner', None)
    if not owner:
        return
    state = hold_state(project)
    receipt = state['receipt']
    bits = []
    if receipt.get('file_name'):
        bits.append(receipt['file_name'])
    if receipt.get('file_bytes_label'):
        bits.append(receipt['file_bytes_label'])
    count = receipt.get('file_count') or 0
    if count:
        bits.append(f'{count} file{"s" if count != 1 else ""}')
    receipt_line = ' · '.join(bits)
    body = state['next_step']
    if receipt_line:
        body = f'{receipt_line}. {body}'
    notify(
        owner,
        'queued',
        f'“{project.title}” is waiting for approval',
        body[:400],
        project.get_absolute_url(),
    )


def owner_hold_payload(project) -> dict:
    """JSON extra fields for /scan-status/ — owner/moderator only."""
    state = hold_state(project)
    receipt = state['receipt']
    return {
        'phase': state['phase'],
        'headline': state['headline'],
        'why_waiting': state['why_waiting'],
        'next_step': state['next_step'],
        'poll': state['poll'],
        'poll_ms': state['poll_ms'],
        'status_label': state['status_label'],
        'file_name': receipt['file_name'],
        'file_bytes': receipt['file_bytes'],
        'file_bytes_label': receipt['file_bytes_label'],
        'file_count': receipt['file_count'],
        'steps': state['steps'],
    }
