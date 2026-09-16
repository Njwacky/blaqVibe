"""
Test Brevo email backend — sends activation and password-reset samples.

Usage:
  python manage.py test_brevo --to you@example.com
  python manage.py test_brevo --to you@example.com --dry-run

The command uses the configured EMAIL_BACKEND. If BREVO_API_KEY is set,
it will POST to https://api.brevo.com/v3/smtp/email. With --dry-run it
only prints the payload without sending.

This is safe to run in production — it does NOT create users or tokens,
it only sends sample emails to the --to address using a dummy user object.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User

from users.emails import send_generic_email


class Command(BaseCommand):
    help = "Test Brevo email backend by sending sample activation & password-reset emails."

    def add_arguments(self, parser):
        parser.add_argument('--to', required=True, help='Recipient email address')
        parser.add_argument('--dry-run', action='store_true', help='Print payload without sending')
        parser.add_argument('--kind', choices=['all', 'activation', 'reset', 'generic'], default='all', help='Which email to test')

    def handle(self, *args, **options):
        to = options['to']
        dry_run = options['dry_run']
        kind = options['kind']

        self.stdout.write(self.style.MIGRATE_HEADING(f"Email backend: {getattr(settings, 'EMAIL_BACKEND', '')}"))
        self.stdout.write(f"BREVO_ENABLED: {getattr(settings, 'BREVO_ENABLED', False)}")
        self.stdout.write(f"BREVO_API_URL: {getattr(settings, 'BREVO_API_URL', '')}")
        self.stdout.write(f"DEFAULT_FROM_EMAIL: {getattr(settings, 'DEFAULT_FROM_EMAIL', '')}")
        self.stdout.write(f"SITE_URL: {getattr(settings, 'SITE_URL', '')}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no email will be sent"))
            # Show what payload would look like via Brevo backend
            from blaqvibes.email_backends import BrevoEmailBackend
            from django.core.mail import EmailMultiAlternatives
            backend = BrevoEmailBackend()
            msg = EmailMultiAlternatives(
                subject="Test — Confirm your BlaqVibes email",
                body="This is a dry-run test.\n\nConfirm: https://blaqvibes.co.za/accounts/verify/dummy/token/\n\n— BlaqVibes",
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za'),
                to=[to],
            )
            msg.attach_alternative("<html><body><h1>Test</h1><p>Confirm your email.</p></body></html>", "text/html")
            payload = backend._build_payload(msg)
            import json
            self.stdout.write(json.dumps(payload, indent=2))
            return

        # For real send, we need a dummy user for template rendering
        # We don't save it — just use in-memory object with needed attrs
        class DummyUser:
            username = "testuser"
            email = to
            pk = 999999

            def get_username(self):
                return self.username

        dummy = DummyUser()

        sent = 0

        if kind in ('all', 'activation'):
            self.stdout.write("→ Sending activation email...")
            from users.emails import send_activation_email
            # Fake request with build_absolute_uri
            class FakeRequest:
                def build_absolute_uri(self, path='/'):
                    base = getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za').rstrip('/')
                    return f"{base}{path}"
                def get_host(self):
                    from urllib.parse import urlparse
                    return urlparse(getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za')).netloc

            ok = send_activation_email(dummy, request=FakeRequest())
            self.stdout.write(self.style.SUCCESS(f"  activation: {'sent' if ok else 'failed'}"))
            sent += int(bool(ok))

        if kind in ('all', 'reset'):
            self.stdout.write("→ Sending password-reset email...")
            from users.emails import send_password_reset_email_via_brevo
            ok = send_password_reset_email_via_brevo(dummy, protocol='https', domain='blaqvibes.co.za', uid='dummyuid', token='dummy-token-123')
            self.stdout.write(self.style.SUCCESS(f"  reset: {'sent' if ok else 'failed'}"))
            sent += int(bool(ok))

        if kind in ('all', 'generic'):
            self.stdout.write("→ Sending generic test email...")
            ok = send_generic_email(
                to_email=to,
                subject="BlaqVibes — Brevo test",
                text_body="This is a test email from BlaqVibes via Brevo.\n\nIf you see this, your BREVO_API_KEY is working.\n\n— BlaqVibes Team",
                html_body="<html><body style='font-family:sans-serif;'><h2>BlaqVibes — Brevo test</h2><p>This is a test email from BlaqVibes via Brevo.</p><p>If you see this, your <strong>BREVO_API_KEY is working</strong>.</p><p>— BlaqVibes Team</p></body></html>",
            )
            self.stdout.write(self.style.SUCCESS(f"  generic: {'sent' if ok else 'failed'}"))
            sent += int(bool(ok))

        if sent == 0:
            raise CommandError("No emails were sent — check BREVO_API_KEY and DEFAULT_FROM_EMAIL (must be verified sender in Brevo dashboard)")

        self.stdout.write(self.style.SUCCESS(f"Done — {sent} email(s) sent to {to}. Check inbox and Brevo dashboard logs."))
