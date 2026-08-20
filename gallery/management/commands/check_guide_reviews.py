"""Check launch guides for staleness.

Usage:
    python manage.py check_guide_reviews              # warn on >90 days
    python manage.py check_guide_reviews --days=30    # stricter ceiling
    python manage.py check_guide_reviews --fail       # exit 1 if any stale

5 Whys:
1. Why a command instead of relying on the UI warning? CI and deploys need a
   machine-readable gate, not a page a human has to open.
2. Why default 90 days? docs/LAUNCH_GUIDE.md promises a quarterly review of
   every URL and claim; older than a quarter is overdue by the project's own
   policy.
3. Why --fail? A cron job or CI step can fail the build when guidance is
   stale, so a forgotten review cannot silently ship stale commands.
4. Why parse with date.fromisoformat? The data stores ISO dates; a typo like
   "20 August 2026" is caught here instead of being compared as a string.
5. Why also list guides missing the key? A guide without last_reviewed was
   never tracked — that is strictly worse than an old date and must surface.
"""

from datetime import date

from django.core.management.base import BaseCommand

from gallery.launch_guides import LAUNCH_GUIDES


class Command(BaseCommand):
    help = "Report launch guides whose last_reviewed is missing or older than --days (default 90)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90, help="Staleness ceiling in days.")
        parser.add_argument("--fail", action="store_true", help="Exit 1 when any guide is stale or untracked.")

    def handle(self, *args, **options):
        days = options["days"]
        today = date.today()
        stale = []
        untracked = []

        for guide in LAUNCH_GUIDES:
            slug = guide.get("slug", "?")
            raw = guide.get("last_reviewed", "")
            if not raw:
                untracked.append(slug)
                continue
            try:
                reviewed = date.fromisoformat(raw)
            except ValueError:
                untracked.append(f"{slug} (unparseable: {raw!r})")
                continue
            age = (today - reviewed).days
            if age > days:
                stale.append((slug, raw, age))

        if untracked:
            self.stdout.write(self.style.WARNING("Untracked guides (missing/unparseable last_reviewed):"))
            for slug in untracked:
                self.stdout.write(f"  - {slug}")

        if stale:
            self.stdout.write(self.style.WARNING(f"Guides older than {days} days:"))
            for slug, raw, age in stale:
                self.stdout.write(f"  - {slug}: last reviewed {raw} ({age} days ago)")

        if not untracked and not stale:
            self.stdout.write(self.style.SUCCESS(f"All {len(LAUNCH_GUIDES)} guides reviewed within {days} days."))

        if options["fail"] and (stale or untracked):
            raise SystemExit(1)
