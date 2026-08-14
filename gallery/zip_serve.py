"""Serve paid ZIPs only through gated views — never via /media/ or .url."""
from django.db.models import F
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


def serve_project_zip(project):
    if not project.zip_file:
        raise Http404
    from .models import AppProject
    AppProject.objects.filter(pk=project.pk).update(clones=F('clones') + 1)
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
