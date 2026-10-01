"""Process the database scan queue without Redis or a Celery worker."""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from gallery.models import ScanJob
from gallery.scan_queue import process_database_job, queue_backend


class Command(BaseCommand):
    help = 'Run saved safety checks without Redis. Run outside the web request; keeps failed/unscanned vibes private.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20, help='Maximum jobs per batch (1–1000).')
        parser.add_argument('--watch', action='store_true', help='Keep processing batches as a standalone worker.')
        parser.add_argument('--poll-interval', type=float, default=5, help='Seconds between batches in watch mode (1–60).')

    def handle(self, *args, **options):
        if queue_backend() != 'database':
            raise CommandError('process_scan_queue requires SCAN_QUEUE_BACKEND=database.')
        limit = options['limit']
        if not 1 <= limit <= 1000:
            raise CommandError('--limit must be between 1 and 1000.')
        interval = options['poll_interval']
        if not 1 <= interval <= 60:
            raise CommandError('--poll-interval must be between 1 and 60 seconds.')
        while True:
            # Outside HTTP there are no request signals to recycle expired
            # connections (particularly important with Supabase's pooler).
            if not connection.in_atomic_block:
                connection.close_if_unusable_or_obsolete()
            # Snapshot the batch: a requeued edit must wait for the next pass,
            # not make this command spin indefinitely on a changing project.
            ids = list(ScanJob.objects.filter(
                status='queued', task_id='', project__status='pending',
            ).order_by('created_at', 'pk').values_list('pk', flat=True)[:limit])
            counts = {'processed': 0, 'failed': 0, 'skipped': 0}
            for job_id in ids:
                counts[process_database_job(job_id)] += 1
            self.stdout.write(
                f"Scans: {counts['processed']} processed, {counts['failed']} failed, {counts['skipped']} skipped."
            )
            if not options['watch']:
                if counts['failed']:
                    raise CommandError('Some scans failed and remain private. Check the server logs before retrying.')
                return
            time.sleep(interval)
