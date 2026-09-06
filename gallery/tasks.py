from celery import shared_task, chain
import subprocess, logging, tempfile, shutil
from django.conf import settings
from django.core.mail import send_mail
from .validators import SECRET_PATTERNS
logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=2, queue='scan', time_limit=120, soft_time_limit=90)
def vulnerability_scan(self, *args, project_id=None):
    """Backend only: dependency audits + Nolo review. No JS sees this.

    The audits are hostile-input work, so they are delegated to
    `gallery.dep_audit`, which runs the tools in a directory of its own against
    pins it derived itself. This task must never hand a tool `cwd=<extracted
    upload>` — that turns an uploaded manifest into an outbound request (or a
    build step) executed by the worker.
    """
    from .models import AppProject
    # Handle chain arg: chain passes previous result as first arg
    if project_id is None and args:
        # args = (prev_result, project_id) or (project_id,)
        project_id = args[-1]
    p = AppProject.objects.get(pk=project_id)
    # Even snippets without zip get Nolo review
    # dep_audit evidence (gallery.trust reads it): 'ran' is True only when
    # an audit actually executed and parsed — so a missing tool or a
    # missing manifest can never be mistaken for a passed check.
    report = {"npm": [], "pip": [], "secrets": [], "dep_audit": {"ran": False, "reason": "no_manifests"}}
    # Dependency NAMES for the slopsquatting check (gallery.dep_check):
    # collected while the ZIP is already extracted — no second read.
    deps = {"npm": [], "pip": []}
    if p.zip_file:
        # ziputil.materialized_path works on local AND S3/R2 storage —
        # FieldFile.path raises NotImplementedError on remote backends.
        from .ziputil import materialized_path
        tmpdir = tempfile.mkdtemp()
        try:
            from .validators import safe_extract_zip
            with materialized_path(p.zip_file) as zip_path:
                safe_extract_zip(zip_path, tmpdir)
            # The audits run in a directory WE own, against pins WE derived,
            # with HOME/npmrc/pip.conf pointed away from the tree. The previous
            # version ran `pip-audit`/`npm audit` with cwd inside the upload, so
            # an attacker-supplied requirements.txt (`--index-url`, `-e .`,
            # `file://`) or `.npmrc` decided where the worker's network traffic
            # went and what got built — code execution on the box holding the
            # DB, Redis, R2 and Paystack credentials. See gallery/dep_audit.
            from .dep_audit import find_manifests, run_dep_audits
            audit = run_dep_audits(tmpdir)
            report["npm"] = list(audit.get('npm') or [])[:10]
            report["pip"] = list(audit.get('pip') or [])[:10]
            report["dep_audit"] = audit.get('dep_audit') or {'ran': False, 'reason': 'no_manifests'}
            # Dependency NAMES for the slopsquatting check come from the same
            # walk (read-only parsers, no tool, no install).
            try:
                from .dep_check import npm_deps_from_manifest, pip_deps_from_requirements
                pkg_path, _lock_path, req_path = find_manifests(tmpdir)
                if pkg_path:
                    deps['npm'] = npm_deps_from_manifest(pkg_path)
                if req_path:
                    deps['pip'] = pip_deps_from_requirements(req_path)
            except Exception:
                logger.info("dep name collection skip %s", p.slug)
        except Exception as e:
            logger.warning("safe extract / audit skip %s: %s", p.slug, e)
            report['extract_error'] = 'unsafe or unreadable zip'
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        # Snippet: no ZIP, no manifest, no installable dependencies — the
        # dep check is vacuously TRUE, and saying so lets an honest snippet
        # earn 'verified' instead of being capped at 'scanned' forever.
        report["dep_audit"] = {"ran": True, "reason": "snippet_no_deps"}
    # Slopsquatting check (gallery.dep_check): ask the real registry whether
    # every dependency name exists. Explicit 404 → flagged (caps the trust
    # tier at 'scanned' via gallery.trust); network failure → treated as
    # existing (fail-open, never a false accusation). Budgeted + cached, so
    # a spam wave costs a constant per hour, never a per-upload bill.
    try:
        from .dep_check import check_dependencies
        outcome = check_dependencies(deps)
        report['dep_exist_check'] = {
            'checked': outcome.get('checked', 0),
            'reason': outcome.get('reason', 'ok'),
        }
        if outcome.get('flagged'):
            report['unknown_deps'] = outcome['flagged'][:10]
            logger.warning("Possible fake packages in %s: %s", p.slug, report['unknown_deps'])
    except Exception:
        logger.exception('dep existence check failed %s', p.slug)
    # Nolo Auto-Review — heuristic or LLM, backend only, crush silently.
    # The profile toggle is checked here (not in the queuing view) because
    # every upload path (publish, edit, git push, fork) funnels through this
    # one task, so one gate covers them all. It defaults True (most creators
    # want instant feedback); "disabled" is recorded in the report rather than
    # skipped, so scan_status and the detail template get a clean value instead
    # of a missing key when asking whether Nolo had an opinion.
    try:
        from .nolo_review import nolo_review
        nolo_enabled = True
        try:
            nolo_enabled = getattr(p.owner.profile, 'nolo_enabled', True)
        except Exception:
            pass
        if nolo_enabled:
            nolo = nolo_review(p)
            report["nolo_review"] = nolo
        else:
            report["nolo_review"] = {"score": None, "fixes": [], "pros": [], "source": "disabled"}
        logger.info(f"Nolo review {p.slug}: {report.get('nolo_review')}")
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
    # Trust badge: the vuln step is the last evidence writer before
    # finalize — grade here so the tier reflects fresh evidence even if
    # finalize is delayed behind other rows in the FIFO queue.
    try:
        from .trust import apply_trust_grade
        apply_trust_grade(p)
    except Exception:
        logger.exception('trust grade after vuln scan failed %s', p.slug)
    return report

@shared_task(bind=True, max_retries=2, queue='scan', time_limit=120, soft_time_limit=90)
def scan_zip_with_clamav(self, project_id):
    """Step 1 of pipeline: ClamAV + secrets. Backend only."""
    from .models import AppProject
    p = AppProject.objects.get(pk=project_id)
    if not p.zip_file:
        return "no_zip"
    # Check the site toggle here (rather than running the scan and ignoring
    # the result): ClamAV is infra, not a user preference, and is CPU-heavy, so
    # a superadmin who disables it expects the pipeline to skip the full scan.
    # Defaults True (security on out of the box); ops turn it off only when the
    # container lacks the ClamAV binary or an external scanner is used.
    try:
        from users.models import SiteSettings
        if not SiteSettings.get().clamav_enabled:
            report = p.scan_report or {}
            report['clamav'] = 'disabled'
            p.scan_report = report
            p.save(update_fields=['scan_report'])
            logger.info(f"ClamAV disabled by site setting — skipping scan for {p.slug}")
            return "clamav_disabled"
    except Exception:
        pass
    # clamscan needs a real filesystem path; on S3/R2 the object is streamed
    # to a temp file for the duration of the scan (ziputil handles both).
    from .ziputil import materialized_path, open_zip
    try:
        with materialized_path(p.zip_file) as zip_path:
            result = subprocess.run(['clamscan', '--no-summary', zip_path], capture_output=True, timeout=30)
        if result.returncode == 1:
            p.status = 'quarantined'
            p.save(update_fields=['status'])
            _apply_trust(p)
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
        _apply_trust(p)
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
        _apply_trust(p)
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

def _apply_trust(p):
    """Write the stored trust tier for this project.

    The ONLY sanctioned writer of AppProject.trust is gallery.trust (see
    its ). 4 points on why a wrapper here: (1) every call site in
    the pipeline gets the same crush-silently guarantee — a badge failure
    can never fail a scan; (2) one import point, so the dependency is
    obvious in this file; (3) it logs with the project slug so a wrong
    tier is traceable in ops; (4) if gallery.trust is ever refactored,
    the pipeline's contract stays this one function.
    """
    try:
        from .trust import apply_trust_grade
        apply_trust_grade(p)
    except Exception:
        logger.exception('trust grade write failed %s', getattr(p, 'slug', '?'))

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
        _apply_trust(p)
        return "quarantined_no_publish"
    report = p.scan_report or {}
    if report.get('clamav') == 'unavailable':
        _set_scan_job(p, 'queued')
        _apply_trust(p)
        return "pending_no_scanner"
    if report.get('clamav') == 'disabled':
        # ClamAV disabled by site admin — skip the scanner check and
        # proceed to the publish logic. Secret scans still run.
        logger.info(f"ClamAV disabled — publishing {p.slug} without virus scan")
    if report.get('secrets'):
        _set_scan_job(p, 'pending')
        _apply_trust(p)
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
        # Classify BEFORE the first appeal score: appeal reads preview_mode,
        # and the feed reads both. Doing it here (not in the view) keeps the
        # optional LLM call off the request path entirely.
        try:
            classify_and_score(p)
        except Exception:
            logger.exception('classify at publish failed %s', p.slug)
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
        try:
            from users.progress import award
            award(p.owner, 'publish', ref=f'project:{p.pk}')
        except Exception:
            logger.exception('publish xp failed %s', p.slug)
    # Update ScanJob for the JS poll (backend only — just a status string).
    _set_scan_job(p, 'clean' if p.status == 'published' else p.status)
    # Trust badge last: every exit path of finalize writes the tier so the
    # stored verdict can never describe a state the row has left. Published
    # → verified/scanned from evidence; held → unknown (renders no badge).
    _apply_trust(p)
    # Email (backend) notifies even when the tab is closed, where a JS toast dies.
    _send_status_email(p)
    return "published" if p.status == 'published' else p.status

def classify_and_score(project):
    """Label the program and give it a starting appeal score.
    """
    from .classify import classify_project
    from .interest import refresh_project
    verdict = classify_project(project)
    try:
        if verdict.get('llm_appeal') is not None and verdict.get('source') not in ('heuristic', 'creator'):
            report = project.scan_report or {}
            report['kind_llm'] = {
                'appeal': verdict.get('llm_appeal'),
                'kind': verdict.get('kind'),
                'source': verdict.get('source'),
            }
            project.scan_report = report
            project.save(update_fields=['scan_report'])
    except Exception:
        logger.exception('storing kind_llm failed')
    refresh_project(project)
    return verdict

@shared_task(queue='rank')
def refresh_appeal_scores(limit=500):
    """Periodic rescore on the 'rank' queue so it never blocks a scan: scans
    are latency-critical for the uploader, ranking is not. The batch `limit`
    lets ops tune batch size to the box without a redeploy; one batched task
    beats a million per-project tasks' broker overhead. It returns the count so
    beat logs show whether the pass is keeping up, and catches everything so a
    ranking failure never retry-storms the broker.
    """
    try:
        from .interest import refresh_batch
        return refresh_batch(limit=limit)
    except Exception:
        logger.exception('refresh_appeal_scores failed')
        return 0

@shared_task(queue='scan')
def process_upload_pipeline(project_id):
    """Master queue: Ensures EVERY app is checked in order, even with 20 concurrent uploads.
    Called via .delay() from publish view — Celery FIFO queue 'scan' serializes.
    No sensitive info leaves backend — JS only gets status poll via /app/<slug>/scan-status/ (clean/pending/quarantined)."""
    # Chain: virus -> vuln -> finalize. If any quarantines, later steps still run but finalize skips publish.
    c = chain(scan_zip_with_clamav.s(project_id), vulnerability_scan.s(project_id), finalize_publish.s(project_id))
    return c.apply_async(queue='scan')

@shared_task(queue='scan')
def run_daily_challenges():
    """Create today's prompt and battle, then pay out closed challenges.

    The daily loop used to run only when somebody happened to open
    /challenges/ — which meant a quiet day could leave yesterday's bounty
    unpaid and today's prompt un-created until a human showed up. The work
    is the same two idempotent calls; only the trigger moved onto the
    clock. Both halves are safe to re-run: ensure_daily_challenge() is
    get_or_create on the day's tag, and settle_past_challenges() skips any
    challenge that already has a winner.
    """
    try:
        from .daily import ensure_daily_battle, ensure_daily_challenge, settle_past_challenges
        challenge = ensure_daily_challenge()
        battle = ensure_daily_battle()
        settled = settle_past_challenges()
        return {
            'tag': getattr(challenge, 'tag', None),
            'battle_id': getattr(battle, 'id', None),
            'settled': len(settled),
        }
    except Exception:
        logger.exception('run_daily_challenges failed')
        return {'tag': None, 'settled': 0}

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
