"""Delete visitor-log rows older than the retention window.

The visitor log is append-only by design (a page view happened; rewriting it
would be a lie). Retention is therefore enforced by deletion on a clock, not by
a flag: `VISITOR_LOG_RETENTION_DAYS` (90 by default) is the promise made to
visitors, and this command is how the promise is kept without a manual sweep.

Run it from cron/Celery beat:

    python manage.py prune_visitor_hits --days 90
    python manage.py prune_visitor_hits --dry-run
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from gallery.models import VisitorHit


class Command(BaseCommand):
    help = 'Delete visitor-log rows older than the retention window.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=None,
            help='Override VISITOR_LOG_RETENTION_DAYS for this run.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be deleted without deleting it.',
        )

    def handle(self, *args, **options):
        days = options.get('days')
        if days is None:
            days = getattr(settings, 'VISITOR_LOG_RETENTION_DAYS', 90)
        if days < 1:
            self.stderr.write(self.style.ERROR('--days must be at least 1.'))
            return

        cutoff = timezone.now() - timedelta(days=days)
        stale = VisitorHit.objects.filter(created_at__lt=cutoff)
        count = stale.count()

        if options.get('dry_run'):
            self.stdout.write(
                f'Would delete {count} visitor row(s) older than '
                f'{cutoff:%Y-%m-%d %H:%M} ({days} days).'
            )
            return

        deleted, _detail = stale.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {count} visitor row(s) older than '
            f'{cutoff:%Y-%m-%d %H:%M} ({days} days); {deleted} total rows removed.'
        ))
