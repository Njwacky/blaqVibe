"""Create or repair a BlaqVibes superadmin — Django flags AND profile.role.

5 Whys: why this module, not just `createsuperuser`?
1. Why doesn't `createsuperuser` work here? It sets is_staff/is_superuser,
   but BlaqVibes gates /admin/dashboard/, /admin/roles/, payouts, etc. on
   `profile.role` (users/decorators.py), which defaults to 'user'. A Django
   superuser with role='user' signs in, then 403s — "admin password never
   works."
2. Why set the role on the Profile too? The app hierarchy
   (moderator < admin < superadmin) is enforced by @superadmin_required;
   Django's is_superuser alone means nothing to those pages.
3. Why email_verified=True? Welcome-star grant and several flows branch
   on profile.email_verified; an internal account has no token to click.
4. Why idempotent (update, never fail on re-run)? Provisioning runs from
   deploy scripts, Docker boots, and seed_demo; the second run must
   converge. Re-running also *repairs* a half-configured admin.
5. Why validate the password? A weak provisioning password would be stored
   silently; validate_password() keeps the same bar as the signup form.

There is no built-in password. `admin` / `youpwassword` is not an account
the app ships with — it looks like a placeholder. Set
DJANGO_SUPERADMIN_PASSWORD (or pass --password) to create one.
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from users.models import AdminLog, Profile

DEFAULT_USERNAME = 'admin'
DEFAULT_EMAIL = 'admin@blaqvibes.co.za'


class ProvisionError(Exception):
    """User-facing failure from provision_superadmin."""


def mark_email_bypassed(user, email=None):
    """Treat an operator mailbox as already confirmed — no token to click.

    5 Whys: why a dedicated helper?
    1. Why bypass at all? create_superadmin / env provision has no inbox.
       Leaving email_verified=False traps the operator behind the
       "confirm your email" banner and blocks trades, tips, payouts.
    2. Why both Profile.email_verified AND allauth EmailAddress? The
       banner and wallet read the Profile flag; allauth email-login and
       password-reset read EmailAddress. One side verified is the
       "I signed in with the email and it still asks me to confirm" bug.
    3. Why not send the real verify mail? There is no token the operator
       can click for an internal address. Setting both flags IS the
       bypass, and AdminLog already records the provision.
    4. Why grant welcome stars here? The grant is bound to a verified
       mailbox. Skipping it would leave the operator at 0 ★ after a
       bypass that pretends the mailbox is real.
    5. Why never steal another user's EmailAddress row? Email is unique
       in allauth. A clash means this address already belongs to someone
       — fail closed and keep Profile.email_verified so our own gates
       still open.
    """
    email = (email or getattr(user, 'email', '') or '').strip().lower()
    if not user or not email:
        return False
    if (user.email or '').strip().lower() != email:
        user.email = email
        user.save(update_fields=['email'])
    try:
        from allauth.account.models import EmailAddress
        existing = EmailAddress.objects.filter(email__iexact=email).first()
        if existing and existing.user_id != user.pk:
            return False
        EmailAddress.objects.filter(user=user, primary=True).exclude(
            email__iexact=email
        ).update(primary=False)
        addr, _ = EmailAddress.objects.get_or_create(
            user=user,
            email=email,
            defaults={'verified': True, 'primary': True},
        )
        if not addr.verified or not addr.primary:
            addr.verified = True
            addr.primary = True
            addr.save(update_fields=['verified', 'primary'])
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'allauth EmailAddress bypass failed for user=%s', getattr(user, 'pk', None)
        )
    profile, _ = Profile.objects.get_or_create(user=user)
    if not profile.email_verified:
        profile.email_verified = True
        profile.save(update_fields=['email_verified'])
    try:
        from users.wallet import grant_welcome_stars
        grant_welcome_stars(user)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'welcome grant on email bypass failed for user=%s', getattr(user, 'pk', None)
        )
    return True


def provision_superadmin(username, email, password, *, skip_password_reset=False):
    """Create or repair the superadmin. Returns (user, created, changed).

    `skip_password_reset=True` keeps an existing user's password (boot/seed
    re-runs must not clobber a password the operator already set). A brand
    new user always gets `password`.
    """
    if not password:
        raise ProvisionError('Empty password — aborting.')
    try:
        validate_password(password)
    except ValidationError as why:
        raise ProvisionError(
            f'Password rejected: {"; ".join(why.messages) if hasattr(why, "messages") else why}'
        ) from why

    User = get_user_model()
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
        if created or not skip_password_reset:
            user.set_password(password)
            changed.append('password')
        user.save()

        profile, _ = Profile.objects.get_or_create(user=user)
        if profile.role != 'superadmin':
            profile.role = 'superadmin'
            changed.append('profile.role')
        if not profile.email_verified:
            profile.email_verified = True
            changed.append('profile.email_verified')
        profile.save(update_fields=['role', 'email_verified'])
        if mark_email_bypassed(user, email) and 'email_bypassed' not in changed:
            changed.append('email_bypassed')

        AdminLog.objects.create(
            actor=user,
            action='create_superadmin' if created else 'repair_superadmin',
            target=f'@{username}: {", ".join(changed) or "no changes"}',
        )

    return user, created, changed


def maybe_provision_from_env(*, ignore_testing=False):
    """Create/repair admin when DJANGO_SUPERADMIN_PASSWORD is set.

    Boot/migrate call this so a .env password actually produces an account
    that can sign in — the missing step behind "I set the admin password
    and login never works." Tests skip it unless ignore_testing=True so a
    leftover env var cannot mint an admin into an unrelated test DB.
    """
    from django.conf import settings

    if not ignore_testing and getattr(settings, 'TESTING', False):
        return None
    password = (os.getenv('DJANGO_SUPERADMIN_PASSWORD') or '').strip()
    if not password:
        return None
    username = (os.getenv('DJANGO_SUPERADMIN_USERNAME') or DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    email = (os.getenv('DJANGO_SUPERADMIN_EMAIL') or DEFAULT_EMAIL).strip() or DEFAULT_EMAIL
    return provision_superadmin(username, email, password, skip_password_reset=True)


def repair_createsuperuser_admin():
    """Give `createsuperuser`'s leftover `admin` the app role it is missing.

    Only touches users that are already Django superusers named `admin`.
    Does not invent a password and does not promote a random account.
    Safe to call from local seed: production operators still use
    create_superadmin / DJANGO_SUPERADMIN_PASSWORD.
    """
    User = get_user_model()
    repaired = []
    for user in User.objects.filter(username__iexact=DEFAULT_USERNAME, is_superuser=True):
        profile, _ = Profile.objects.get_or_create(user=user)
        fields = []
        if profile.role != 'superadmin':
            profile.role = 'superadmin'
            fields.append('role')
        if not profile.email_verified:
            profile.email_verified = True
            fields.append('email_verified')
        if fields:
            profile.save(update_fields=fields)
        mark_email_bypassed(user, user.email or DEFAULT_EMAIL)
        if fields:
            AdminLog.objects.create(
                actor=user,
                action='repair_superadmin',
                target=f'@{user.username}: {", ".join("profile." + f for f in fields)}',
            )
            repaired.append(user.username)
    return repaired
