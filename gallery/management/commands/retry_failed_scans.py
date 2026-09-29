"""Recover saved uploads after Redis/scan dispatch is restored."""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from gallery.models import ScanJob
from gallery.scan_queue import enqueue_scan


class Command(BaseCommand):
    help = 'Requeue failed scans for saved, pending ZIP uploads. Never publishes or duplicates a vibe.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100, help='Maximum jobs to retry (1–1000).')
        parser.add_argument('--include-stale', action='store_true',
                            help='Also recover queued jobs with no task ID older than five minutes (pre-fix timeouts).')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 1000:
            raise CommandError('--limit must be between 1 and 1000.')
        eligible = Q(status='failed')
        if options['include_stale']:
            eligible |= Q(status='queued', task_id='', updated_at__lt=timezone.now() - timedelta(minutes=5))
        jobs = (ScanJob.objects
                .filter(eligible, project__status='pending')
                .exclude(project__zip_file='').exclude(project__zip_file__isnull=True)
                .select_related('project').order_by('updated_at', 'pk')[:limit])
        requeued = 0
        for job in jobs:
            # Claim with a conditional UPDATE so simultaneous command runs
            # cannot both dispatch the same failed row.
            claimed = ScanJob.objects.filter(eligible, pk=job.pk, project__status='pending').update(
                status='queued', task_id='', updated_at=timezone.now(),
            )
            if not claimed:
                continue
            if not enqueue_scan(job.project):
                raise CommandError(
                    f'Scan queue is unavailable; job {job.pk} remains failed. '
                    f'Requeued {requeued} job(s) before stopping. Check the server logs.'
                )
            requeued += 1
        self.stdout.write(self.style.SUCCESS(f'Requeued {requeued} failed scan(s).'))
