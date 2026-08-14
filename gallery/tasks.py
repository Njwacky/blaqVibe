from celery import shared_task, chain
import subprocess, os, zipfile, logging, json, re, tempfile, shutil
from django.conf import settings
from django.core.mail import send_mail
from .validators import SECRET_PATTERNS
logger = logging.getLogger(__name__)


# 5 Whys Queue: Why chain not parallel?
# 1. Every app MUST be checked — chain guarantees order: virus -> secrets -> vuln -> publish
# 2. Why one queue 'scan'? Single worker processes one at a time, no race, fair FIFO.
# 3. Why acks_late + retry? Concurrent uploads = worker OOM mid-scan → requeue, not lost.
# 4. Why backend never JS? JS is view-source visible — secrets in JS = breach.

@shared_task(bind=True, max_retries=2, queue='scan', time_limit=120, soft_time_limit=90)
def vulnerability_scan(self, *args, project_id=None):
    """Backend only: npm audit / pip-audit + Nolo review. No JS sees this."""
    from .models import AppProject
    # Handle chain arg: chain passes previous result as first arg
    if project_id is None and args:
        # args = (prev_result, project_id) or (project_id,)
        project_id = args[-1]
    p = AppProject.objects.get(pk=project_id)
    # Even snippets without zip get Nolo review
    report = {"npm": [], "pip": [], "secrets": []}
    if p.zip_file:
        # ziputil.materialized_path works on local AND S3/R2 storage —
        # FieldFile.path raises NotImplementedError on remote backends.
        from .ziputil import materialized_path
        tmpdir = tempfile.mkdtemp()
        try:
            from .validators import safe_extract_zip
            with materialized_path(p.zip_file) as zip_path:
                safe_extract_zip(zip_path, tmpdir)
            for root, _, files in os.walk(tmpdir):
                if 'package.json' in files:
                    try:
                        r = subprocess.run(['npm', 'audit', '--json'], cwd=root, capture_output=True, timeout=30)
                        if r.stdout:
                            data = json.loads(r.stdout.decode(errors='ignore') or '{}')
                            vulns = data.get('vulnerabilities', {})
                            report["npm"] = list(vulns.keys())[:10]
                    except Exception as e:
                        logger.info(f"npm audit skip {p.slug}: {e}")
                    break
                if 'requirements.txt' in files:
                    try:
                        r = subprocess.run(['pip-audit', '-f', 'json'], cwd=root, capture_output=True, timeout=30)
                        if r.stdout:
                            data = json.loads(r.stdout.decode(errors='ignore') or '[]')
                            report["pip"] = [d.get('name') for d in data][:10]
                    except FileNotFoundError:
                        pass
                    except Exception as e:
                        logger.info(f"pip audit skip {p.slug}: {e}")
                    break
        except Exception as e:
            logger.warning("safe extract / audit skip %s: %s", p.slug, e)
            report['extract_error'] = 'unsafe or unreadable zip'
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    # Nolo Auto-Review — heuristic or LLM, backend only, crush silently
    try:
        from .nolo_review import nolo_review
        nolo = nolo_review(p)
        report["nolo_review"] = nolo
        logger.info(f"Nolo review {p.slug}: {nolo}")
    except Exception as e:
        logger.exception(f"Nolo review crush {p.slug}: {e}")
        report["nolo_review"] = {"score": 5, "fixes": [], "pros": [], "source": "error"}
    # Merge with existing scan_report (don't overwrite)
    try:
        existing = p.scan_report or {}
        existing.update(report)
        p.scan_report = existing
        p.save(update_fields=['scan_report'])
    except Exception:
        try:
            p.scan_report = report
            p.save(update_fields=['scan_report'])
        except Exception: pass
    logger.info(f"Vuln scan {p.slug}: {report}")
    return report

@shared_task(bind=True, max_retries=2, queue='scan', time_limit=120, soft_time_limit=90)
def scan_zip_with_clamav(self, project_id):
    """Step 1 of pipeline: ClamAV + secrets. Backend only."""
    from .models import AppProject
    p = AppProject.objects.get(pk=project_id)
    if not p.zip_file:
        return "no_zip"
    # clamscan needs a real filesystem path; on S3/R2 the object is streamed
    # to a temp file for the duration of the scan (ziputil handles both).
    from .ziputil import materialized_path, open_zip
    try:
        with materialized_path(p.zip_file) as zip_path:
            result = subprocess.run(['clamscan', '--no-summary', zip_path], capture_output=True, timeout=30)
        if result.returncode == 1:
            p.status = 'quarantined'
            p.save(update_fields=['status'])
            logger.warning(f"Virus quarantined {p.slug}")
            from .notify import notify
            notify(p.owner, 'quarantined', f'“{p.title}” was quarantined', 'Virus or blocked secret found. Edit and re-upload a clean ZIP.', p.get_absolute_url())
            return "quarantined"
    except FileNotFoundError:
        logger.warning(f"clamscan missing — leaving pending {p.slug}")
        report = p.scan_report or {}
        report['clamav'] = 'unavailable'
        p.scan_report = report
        p.status = 'pending'
        p.save(update_fields=['scan_report', 'status'])
        from .notify import notify
        notify(p.owner, 'quarantined', f'“{p.title}” needs human review', 'Virus scanner is offline. We did not auto-publish.', p.get_absolute_url())
        return "scanner_unavailable"
    except subprocess.TimeoutExpired:
        logger.warning(f"ClamAV timeout {p.slug}, retry")
        raise self.retry()
    # Secrets scan — backend only, never sent to JS. Reads via storage API,
    # so it works identically on local disk and S3/R2.
    secrets = []
    try:
        with open_zip(p.zip_file) as z:
            for name in z.namelist():
                if name.lower().endswith(('.py','.js','.env','.txt','.json','.md')):
                    try:
                        text = z.read(name).decode('utf-8', errors='ignore')
                        for pat in SECRET_PATTERNS:
                            if pat.search(text):
                                secrets.append(name)  # store filename only, not key
                                break
                    except Exception: pass
    except Exception: pass
    if secrets:
        logger.warning(f"Secrets in {p.slug}: {secrets[:3]}")
        # Keep pending for human review, don't auto-publish. Store filenames only
        # (never the secret values) so owner_scan_reason can explain the hold.
        report = p.scan_report or {}
        report['secrets'] = secrets
        p.scan_report = report
        p.status = 'pending'
        p.save(update_fields=['scan_report', 'status'])
        from .notify import notify
        notify(p.owner, 'quarantined', f'“{p.title}” needs human review',
               'Possible secrets detected in the ZIP. A moderator will review it before it goes live.',
               p.get_absolute_url())
        return "secrets_found"
    return "clean"

def _set_scan_job(p, status):
    try:
        from .models import ScanJob
        job, _ = ScanJob.objects.get_or_create(project=p)
        job.status = status
        job.save(update_fields=['status'])
    except Exception:
        logger.exception('scan job update failed')


def _send_status_email(p):
    try:
        if p.owner.email:
            site = getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za')
            if p.status == 'published':
                subject = f"✓ Your vibe “{p.title}” is live on BlaqVibes!"
            else:
                subject = f"⏳ Your vibe “{p.title}” needs review"
            msg = (
                f"Hi @{p.owner.username},\n\n"
                f"Your vibe '{p.title}' ({p.slug}) is {p.status}.\n\n"
                f"View: {site}/app/{p.slug}/\n"
                f"My Vibes: {site}/my-vibes/\n\n"
                f"BlaqVibes — Publish the Vibes.\n"
            )
            send_mail(subject, msg, getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za'), [p.owner.email], fail_silently=True)
    except Exception as e:
        logger.warning(f"Email fail {p.slug}: {e}")


@shared_task(queue='scan')
def finalize_publish(*args, project_id=None):
    """Final step: tree + status → published. Runs after scans.

    A clean scan publishes. Missing scanner or detected secrets hold the vibe
    in `pending` for human review — never auto-publish those.
    """
    from .models import AppProject, AppFile
    if project_id is None and args:
        project_id = args[-1]
    p = AppProject.objects.get(pk=project_id)
    if p.status == 'quarantined':
        return "quarantined_no_publish"
    report = p.scan_report or {}
    if report.get('clamav') == 'unavailable':
        _set_scan_job(p, 'queued')
        return "pending_no_scanner"
    if report.get('secrets'):
        _set_scan_job(p, 'pending')
        return "pending_secrets"
    if not p.file_tree and p.zip_file:
        try:
            from .ziputil import build_tree
            tree, file_list = build_tree(p.zip_file)
            p.file_tree = tree
            p.file_count = len(file_list)
            p.save(update_fields=['file_tree', 'file_count'])
            for f in file_list[:2000]:
                AppFile.objects.get_or_create(project=p, path=f['path'], defaults={'size': f['size']})
        except Exception as e:
            logger.error(f"Tree rebuild fail {p.slug}: {e}")
    if p.status == 'pending':
        p.status = 'published'
        p.save(update_fields=['status'])
    if p.status == 'published':
        from .notify import notify
        # Close the publish → launch loop: detect the shippable artifact in
        # the ZIP and point the creator at the matching launch guide.
        launch_hint = ''
        launch_url = p.get_absolute_url()
        try:
            from .artifact_detect import artifact_route, detect_artifact
            artifact = detect_artifact(p)
            route = artifact_route(artifact) if artifact else None
            if route:
                launch_hint = f"Next: launch it as a {route['name']} — guides inside."
                launch_url = f'/launch/?artifact={artifact}'
        except Exception:
            logger.exception('artifact detect failed %s', p.slug)
        notify(p.owner, 'published', f'“{p.title}” is live', launch_hint, launch_url)
    # Update ScanJob for the JS poll (backend only — just a status string).
    _set_scan_job(p, 'clean' if p.status == 'published' else p.status)
    # Email notify — Why backend? JS toast dies when tab closed, email persists.
    _send_status_email(p)
    return "published" if p.status == 'published' else p.status

@shared_task(queue='scan')
def process_upload_pipeline(project_id):
    """Master queue: Ensures EVERY app is checked in order, even with 20 concurrent uploads.
    Called via .delay() from publish view — Celery FIFO queue 'scan' serializes.
    No sensitive info leaves backend — JS only gets status poll via /app/<slug>/scan-status/ (clean/pending/quarantined)."""
    # Chain: virus -> vuln -> finalize. If any quarantines, later steps still run but finalize skips publish.
    c = chain(scan_zip_with_clamav.s(project_id), vulnerability_scan.s(project_id), finalize_publish.s(project_id))
    return c.apply_async(queue='scan')

@shared_task
def generate_weekly_challenges():
    """Weekly Celery beat — AI drafts 3 challenges, deduped, is_active=False for superadmin approve. Backend only."""
    try:
        from .challenge_ai import create_draft_challenges
        created = create_draft_challenges()
        # Optionally notify superadmin via email
        if created:
            try:
                from django.contrib.auth.models import User
                from django.core.mail import send_mail
                from django.conf import settings
                supers = User.objects.filter(profile__role='superadmin')
                emails = [u.email for u in supers if u.email]
                if emails:
                    site = getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za')
                    send_mail(f"BlaqVibes: {len(created)} draft challenges ready", f"AI drafted {len(created)} challenges. Approve at {site}/challenges/", getattr(settings, 'DEFAULT_FROM_EMAIL','noreply@blaqvibes.co.za'), emails, fail_silently=True)
            except Exception: pass
        return len(created)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"generate_weekly_challenges crush: {e}")
        return 0
