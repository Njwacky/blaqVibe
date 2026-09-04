"""Check launch guides for staleness.
    python manage.py check_guide_reviews              # warn on >90 days
    python manage.py check_guide_reviews --days=30    # stricter ceiling
    python manage.py check_guide_reviews --fail       # exit 1 if any stale
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
