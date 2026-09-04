"""Re-label and re-score published vibes.
"""
import collections

from django.core.management.base import BaseCommand

from gallery.interest import refresh_project
from gallery.models import AppProject

class Command(BaseCommand):
    help = 'Re-detect program kind and recompute appeal score for vibes.'

    def add_arguments(self, parser):
        parser.add_argument('--llm', action='store_true',
                            help='Allow LLM classification for low-confidence rows (costs money).')
        parser.add_argument('--force', action='store_true',
                            help='Also overwrite rows whose kind came from a creator or moderator.')
        parser.add_argument('--limit', type=int, default=0, help='Max rows (0 = all).')
        parser.add_argument('--status', default='published', help="Status filter, or 'all'.")

    def handle(self, *args, **opts):
        from gallery.classify import classify_project

        qs = AppProject.objects.all().order_by('pk')
        if opts['status'] != 'all':
            qs = qs.filter(status=opts['status'])
        if not opts['force']:
            qs = qs.exclude(kind_source__in=['creator', 'moderator'])
        if opts['limit']:
            qs = qs[:opts['limit']]

        counts = collections.Counter()
        total = 0
        for project in qs.iterator(chunk_size=100):
            verdict = classify_project(project, allow_llm=opts['llm'])
            refresh_project(project)
            counts[verdict['kind']] += 1
            total += 1
        self.stdout.write(self.style.SUCCESS(f'Reclassified {total} vibes.'))
        for kind, n in counts.most_common():
            self.stdout.write(f'  {kind}: {n}')
