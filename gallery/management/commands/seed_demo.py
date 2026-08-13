from django.core.management.base import BaseCommand

from gallery.seed import seed_demo


class Command(BaseCommand):
    help = 'Load published demo vibes so the feed is not an empty grid.'

    def handle(self, *args, **options):
        stats = seed_demo()
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo catalog ready — {stats['published']} published "
                f"({stats['created']} new this run)."
            )
        )
