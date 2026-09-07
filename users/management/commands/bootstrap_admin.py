"""Bootstrap a production BlaqVibes superadmin from Render environment variables.

This command is intentionally opt-in. It is useful on hosts such as Render Free
where an interactive shell is unavailable. Credentials stay in the host's
secret environment and are never committed to the repository.
"""
import os

from django.core.management.base import BaseCommand, CommandError

from users.provision import (
    DEFAULT_EMAIL,
    DEFAULT_USERNAME,
    ProvisionError,
    provision_superadmin,
)


class Command(BaseCommand):
    help = "Create or repair the production superadmin from environment variables."

    def handle(self, *args, **options):
        enabled = (os.getenv("BOOTSTRAP_ADMIN") or "").strip().lower()
        if enabled != "true":
            self.stdout.write(
                "Admin bootstrap skipped: set BOOTSTRAP_ADMIN=true to enable it."
            )
            return

        username = (os.getenv("OWNER_USERNAME") or DEFAULT_USERNAME).strip()
        email = (os.getenv("OWNER_EMAIL") or DEFAULT_EMAIL).strip()
        password = os.getenv("OWNER_PASSWORD") or ""

        missing = [
            name
            for name, value in (
                ("OWNER_USERNAME", username),
                ("OWNER_EMAIL", email),
                ("OWNER_PASSWORD", password),
            )
            if not value
        ]
        if missing:
            raise CommandError(
                "Admin bootstrap is enabled but required environment variable(s) "
                f"are missing: {', '.join(missing)}"
            )

        try:
            user, created, changed = provision_superadmin(
                username,
                email,
                password,
                skip_password_reset=True,
            )
        except ProvisionError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} superadmin @{user.username}. "
                "is_staff=True, is_superuser=True, profile.role=superadmin. "
                f"Changed: {', '.join(changed) or 'none'}."
            )
        )
        self.stdout.write(
            "Remove BOOTSTRAP_ADMIN, OWNER_USERNAME, OWNER_EMAIL and OWNER_PASSWORD "
            "from the Render environment after the successful deploy."
        )
