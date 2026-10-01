"""Durable scan dispatch. Upload requests save work; they never run scanners."""
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import AppProject, ScanJob
from .upload_diagnostics import UploadTrace

logger = logging.getLogger(__name__)


def queue_backend():
    return getattr(settings, 'SCAN_QUEUE_BACKEND', 'database')


def enqueue_scan(project, trace=None):
    """Accept a scan into the database, or dispatch to optional Celery.

    True means the job was accepted, NOT that a scanner has started. Database
    jobs are consumed by process_scan_queue outside HTTP requests. No eager
    fallback or automatic publication is used when a service is unavailable.
    """
    trace = trace or UploadTrace('scan-retry')
    trace.project_id = project.pk
    job = None
    try:
        with trace.stage('queue_scan'):
            job, _ = ScanJob.objects.update_or_create(
                project=project, defaults={'status': 'queued', 'task_id': ''},
            )
            if queue_backend() == 'database':
                return True

            from .tasks import process_upload_pipeline
            # Optional broker publishing stays bounded and has no in-request
            # scan fallback. Install requirements-celery.txt before opting in.
            transport_options = dict(process_upload_pipeline.app.conf.broker_transport_options or {})
            transport_options.update(max_retries=0, connect_retries_timeout=3)
            with process_upload_pipeline.app.connection_for_write(
                connect_timeout=3, transport_options=transport_options,
            ) as connection:
                task = process_upload_pipeline.apply_async(
                    args=[project.pk], connection=connection, retry=False, ignore_result=True,
                )
            ScanJob.objects.filter(pk=job.pk).update(
                task_id=getattr(task, 'id', '') or '', updated_at=timezone.now(),
            )
            # A fast worker may already have written a final verdict.
            ScanJob.objects.filter(pk=job.pk, status='queued').update(
                status='scanning', updated_at=timezone.now(),
            )
        return True
    except Exception:
        if job is None:
            raise  # A database error is not a recoverable broker outage.
        ScanJob.objects.filter(pk=job.pk).update(status='failed', updated_at=timezone.now())
        return False


def process_database_job(job_id):
    """Claim one unsent job and run the normal checks without a broker.

    A conditional UPDATE is the claim: competing runners cannot both process
    the same row. A token and immutable archive name also prevent a runner
    from publishing newer bytes that were edited/requeued during its checks.
    This function is for management commands, never a web view.
    """
    from .tasks import attention_check, finalize_publish, scan_zip_with_clamav, vulnerability_scan
    from .scan_safety import ScanSuperseded, scan_project

    token = f'database:{uuid.uuid4().hex}'
    claimed = ScanJob.objects.filter(
        pk=job_id, status='queued', task_id='', project__status='pending',
    ).update(status='scanning', task_id=token, updated_at=timezone.now())
    if not claimed:
        return 'skipped'

    trace = UploadTrace('database-scan')
    try:
        job = ScanJob.objects.select_related('project').get(pk=job_id)
        project = job.project
        trace.project_id = project.pk
        archive = project.zip_file.name if project.zip_file else ''
        snippet = (project.html_code, project.css_code, project.js_code)
        claim = {'job_id': job_id, 'task_id': token, 'archive': archive, 'snippet': snippet}
        with trace.stage('virus_scan'):
            # apply() is explicit local execution; no delay(), broker, or
            # Celery chain. Existing subprocess/network limits still apply.
            scan_zip_with_clamav.apply(args=[project.pk], kwargs={'scan_claim': claim}, throw=True)
            if not archive:
                from .trust import snippet_evidence
                with scan_project(project.pk, claim) as current:
                    snippet_evidence(current)
        with trace.stage('vulnerability_scan'):
            vulnerability_scan.apply(args=[project.pk], kwargs={'scan_claim': claim}, throw=True)

        with trace.stage('finalize_scan'), transaction.atomic():
            # Lock in the same order as an edit (project, then scan job). Only
            # finalization is transactional; slow scans hold no database lock.
            current = AppProject.objects.select_for_update().get(pk=project.pk)
            current_job = ScanJob.objects.select_for_update().get(pk=job_id)
            if current_job.task_id != token:
                return 'skipped'  # A replacement upload owns the queue now.
            current_archive = current.zip_file.name if current.zip_file else ''
            if (current_archive != archive or
                    (not archive and (current.html_code, current.css_code, current.js_code) != snippet)):
                ScanJob.objects.filter(pk=job_id, task_id=token).update(
                    status='queued', task_id='', updated_at=timezone.now(),
                )
                return 'skipped'
            if current.status not in ('pending', 'quarantined'):
                return 'skipped'  # Never resurrect a removed project.
            snippet_secrets = (current.scan_report or {}).get('snippet_scan', {}).get('secrets_found')
            if snippet_secrets:
                ScanJob.objects.filter(pk=job_id, task_id=token).update(
                    status='pending', updated_at=timezone.now(),
                )
            else:
                finalize_publish.apply(kwargs={'project_id': project.pk}, throw=True)
                current.refresh_from_db(fields=['status'])
                # Missing scanners await review; they are not reprocessed on
                # every poll and are never labelled as having passed a scan.
                status = {'published': 'clean', 'quarantined': 'quarantined'}.get(current.status, 'pending')
                ScanJob.objects.filter(pk=job_id, task_id=token).update(
                    status=status, updated_at=timezone.now(),
                )
        try:
            attention_check.apply(args=[project.pk], throw=True)
        except Exception:
            # Attention/retention is not a safety verdict. A cosmetic failure
            # after successful finalization must not turn a clean job failed.
            logger.exception('post-scan attention check failed project_id=%s', project.pk)
        return 'processed'
    except ScanSuperseded:
        # A content change without an enqueue must still get a fresh scan.
        # Never alter a replacement claim or resurrect a removed project.
        ScanJob.objects.filter(pk=job_id, task_id=token, project__status='pending').update(
            status='queued', task_id='', updated_at=timezone.now(),
        )
        ScanJob.objects.filter(pk=job_id, task_id=token).update(
            status='pending', updated_at=timezone.now(),
        )
        return 'skipped'
    except (AppProject.DoesNotExist, ScanJob.DoesNotExist):
        return 'skipped'
    except Exception:
        logger.exception('database scan failed ref=%s job_id=%s', trace.reference, job_id)
        ScanJob.objects.filter(pk=job_id, task_id=token).update(status='failed', updated_at=timezone.now())
        return 'failed'
