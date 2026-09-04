"""Create today's daily challenge and settle the days that just closed.

Celery beat runs this at 00:05 (``gallery.tasks.run_daily_challenges``).
This command exists for the same reason every scheduled job here has one:
a deployment without beat (or an operator who needs it *now*) should not
have to open /challenges/ to make the loop turn.

Both halves are idempotent — running it twice in a day is a no-op, which
is the only safe shape for a job that a cron might fire twice.
"""
from django.core.management.base import BaseCommand

from gallery.daily import ensure_daily_challenge, settle_past_challenges

class Command(BaseCommand):
    help = "Ensure today's daily challenge exists and pay out finished days."

    def add_arguments(self, parser):
        parser.add_argument(
            '--settle-only', action='store_true',
            help="Only pay out finished challenges; do not create today's.")
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would happen; write nothing.')

    def handle(self, *args, **options):
        settle_only = options.get('settle_only')
        dry_run = options.get('dry_run')
        verbosity = options.get('verbosity', 1)

        if dry_run:
            from gallery.daily import today_challenge
            challenge = today_challenge()
            self.stdout.write(
                f"today: {getattr(challenge, 'tag', None) or 'no challenge row yet'}"
                f" — {getattr(challenge, 'title', '')}".strip())
            self.stdout.write('dry run — nothing written')
            return

        if not settle_only:
            challenge = ensure_daily_challenge()
            if challenge and verbosity:
                self.stdout.write(f"today's challenge: {challenge.tag} — {challenge.title}")

        settled = settle_past_challenges()
        for challenge, winner in settled:
            self.stdout.write(
                self.style.SUCCESS(
                    f'settled {challenge.tag}: {winner.title} by @{winner.owner.username}'))
        if verbosity and not settled:
            self.stdout.write('nothing to settle')
