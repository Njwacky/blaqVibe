"""Create or repair the BlaqVibes superadmin — ALL flags, atomically.

See users/provision.py for the 5 Whys. This command is the operator
interface; seed/migrate call the same function so a password in .env
actually produces an account that can sign in.
"""
import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from users.provision import (
    DEFAULT_EMAIL,
    DEFAULT_USERNAME,
    ProvisionError,
    provision_superadmin,
)


class Command(BaseCommand):
    help = ('Create or update the superadmin: Django is_staff/is_superuser, '
            'profile.role="superadmin", email_verified=True. Idempotent.')

    def add_arguments(self, parser):
        parser.add_argument('--username', default=DEFAULT_USERNAME)
        parser.add_argument('--email', default=DEFAULT_EMAIL)
        parser.add_argument('--password', default=None,
                            help='Omit to prompt, or set DJANGO_SUPERADMIN_PASSWORD')
        parser.add_argument('--skip-password-reset', action='store_true',
                            help='Existing user keeps their current password')

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']

        password = options['password']
        if not password:
            password = os.getenv('DJANGO_SUPERADMIN_PASSWORD')
        if not password:
            password = getpass.getpass(f"Password for {username!r}: ")
        if not password:
            raise CommandError('Empty password — aborting.')

        try:
            user, created, changed = provision_superadmin(
                username, email, password,
                skip_password_reset=options['skip_password_reset'],
            )
        except ProvisionError as why:
            raise CommandError(str(why)) from why

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} superadmin @{user.username} "
            f"({user.email}) — role=superadmin, email_verified=True, "
            f"is_staff=True, is_superuser=True. Changed: {', '.join(changed) or 'none'}."
        ))
