"""Bounded scan dispatch. A broker outage must never turn a web worker into a scanner."""
from django.utils import timezone

from .models import ScanJob
from .upload_diagnostics import UploadTrace


def enqueue_scan(project, trace=None):
    """Return True if dispatched; otherwise retain a failed job for recovery.

    Scanning remains an explicit eager-mode convenience in development, but
    there is NO eager fallback when publishing to a real broker fails. One
    ClamAV scan alone can consume Gunicorn's entire 30-second request budget.
    """
    from .tasks import process_upload_pipeline

    trace = trace or UploadTrace('scan-retry')
    trace.project_id = project.pk
    job = None
    try:
        with trace.stage('queue_scan'):
            job, _ = ScanJob.objects.get_or_create(project=project, defaults={'status': 'queued'})
            # retry=False bounds publishing, but Kombu's lazy default_channel
            # has a SEPARATE connection retry loop. Bound that on this producer
            # too, without changing the worker's reconnection policy.
            transport_options = dict(process_upload_pipeline.app.conf.broker_transport_options or {})
            transport_options.update(max_retries=0, connect_retries_timeout=3)
            with process_upload_pipeline.app.connection_for_write(
                connect_timeout=3, transport_options=transport_options,
            ) as connection:
                # ScanJob is our result store. Subscribing to Redis results here
                # used to add another long reconnect loop before dispatch.
                task = process_upload_pipeline.apply_async(
                    args=[project.pk], connection=connection, retry=False, ignore_result=True,
                )
            ScanJob.objects.filter(pk=job.pk).update(
                task_id=getattr(task, 'id', '') or '', updated_at=timezone.now(),
            )
            # A fast/eager worker may already have written the verdict. Do
            # not overwrite clean/pending/quarantined with stale 'scanning'.
            ScanJob.objects.filter(pk=job.pk, status='queued').update(
                status='scanning', updated_at=timezone.now(),
            )
        return True
    except Exception:
        if job is None:
            # DB errors cannot be repaired here. Let the request trace report
            # them rather than pretending that a recoverable job exists.
            raise
        ScanJob.objects.filter(pk=job.pk).update(status='failed', updated_at=timezone.now())
        return False
