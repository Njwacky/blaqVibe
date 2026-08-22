"""Create or repair the BlaqVibes superadmin — ALL flags, atomically.

5 Whys: why a command instead of `createsuperuser`?
1. Why doesn't `createsuperuser` work here? It sets is_staff/is_superuser,
   but BlaqVibes gates admin pages on `profile.role` (users/decorators.py),
   which defaults to 'user'. A Django superuser with role='user' gets 403 on
   /users/admin/* — "I added an admin but it never works."
2. Why set the role on the Profile too? The app's own hierarchy
   (moderator < admin < superadmin) is enforced by @superadmin_required and
   gallery middleware; Django's is_superuser alone means nothing to them.
3. Why email_verified=True? The welcome-star grant and several flows branch
   on profile.email_verified; an unverifiable internal account must not be
   stuck in "pending" forever. There is no token to click, so it is set
   directly — that IS the bypass, recorded in AdminLog for the audit trail.
4. Why idempotent (update, never fail on re-run)? Provisioning runs in
   deploy scripts and Docker boots; the second run must converge, not crash
   on a duplicate username. Re-running also *repairs* a half-configured
   admin (e.g. one created earlier without the role).
5. Why validate the password? A weak provisioning password would be stored
   silently; validate_password() keeps the same bar as the signup form.
   A known deviation (--password / env) is echoed once, never stored.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from users.models import AdminLog, Profile

DEFAULT_USERNAME = 'admin'
DEFAULT_EMAIL = 'admin@blaqvibes.co.za'


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
        User = get_user_model()
        username = options['username']
        email = options['email']

        password = options['password']
        if not password:
            import os
            password = os.getenv('DJANGO_SUPERADMIN_PASSWORD')
        if not password:
            import getpass
            password = getpass.getpass(f"Password for {username!r}: ")
        if not password:
            raise CommandError('Empty password — aborting.')
        try:
            validate_password(password)
        except Exception as why:
            raise CommandError(f'Password rejected: {"; ".join(why.messages) if hasattr(why, "messages") else why}')

        with transaction.atomic():
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'email': email},
            )
            changed = []
            if user.email != email:
                user.email = email
                changed.append('email')
            if not user.is_staff:
                user.is_staff = True
                changed.append('is_staff')
            if not user.is_superuser:
                user.is_superuser = True
                changed.append('is_superuser')
            if created or not options['skip_password_reset']:
                user.set_password(password)
                changed.append('password')
            user.save()

            # The part createsuperuser never does — the app's own role gate.
            profile, _ = Profile.objects.get_or_create(user=user)
            if profile.role != 'superadmin':
                profile.role = 'superadmin'
                changed.append('profile.role')
            if not profile.email_verified:
                profile.email_verified = True
                changed.append('profile.email_verified')
            profile.save(update_fields=['role', 'email_verified'])

            AdminLog.objects.create(
                actor=user,
                action='create_superadmin' if created else 'repair_superadmin',
                target=f'@{username}: {", ".join(changed) or "no changes"}',
            )

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} superadmin @{username} "
            f"({email}) — role=superadmin, email_verified=True, "
            f"is_staff=True, is_superuser=True. Changed: {', '.join(changed) or 'none'}."
        ))
