"""`python manage.py security_check` — is this process fit to face the public internet?
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand

DEV_SECRET_KEY = 'django-insecure-blaqvibes-dev-key-change-in-prod-07070A'
DEV_HOST_SUFFIXES = ('.e2b.app', '.e2b.dev', 'localhost', '127.0.0.1', '0.0.0.0', 'testserver')
MIN_HSTS_SECONDS = 1_555_200  # 180 days

class Command(BaseCommand):
    help = (
        'Audit the running settings for production hardening. Exits 1 on any '
        'ERROR. In a dev posture (DEBUG=1 or DJANGO_LOCAL_DEV=1) findings are '
        'reported as warnings so local work is never blocked.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--strict', action='store_true',
            help='Treat warnings as errors too (for CI gates that want zero findings).',
        )
        parser.add_argument(
            '--as-production', action='store_true',
            help='Evaluate as production even if DEBUG/LOCAL_DEV are on.',
        )

    def handle(self, *args, **options):
        dev = bool(getattr(settings, 'DEBUG', False) or getattr(settings, 'LOCAL_DEV', False))
        production = options['as_production'] or not dev
        errors, warnings = self.collect(production=production, dev=dev)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'security_check — posture: {"production" if production else "development"}'
        ))
        for item in errors:
            self.stdout.write(f'  {self.style.ERROR("ERROR")}: {item}')
        for item in warnings:
            self.stdout.write(f'  {self.style.WARNING("WARN")} : {item}')
        if not errors and not warnings:
            self.stdout.write(self.style.SUCCESS('  ok — no findings'))

        blocking = list(errors)
        if options['strict']:
            blocking += warnings
        if not production:
            self.stdout.write(self.style.WARNING(
                '  dev posture: hardening gates are off by design here. '
                'Re-run with --as-production to see what this config would do on a public host.'
            ))
            blocking = []
        if blocking:
            raise SystemExit(
                f'security_check failed: {len(blocking)} finding(s). '
                'Fix the environment/settings before exposing this host.'
            )
        return None

    @staticmethod
    def _is_dev_host(host: str) -> bool:
        clean = host.lstrip('.').lower()
        return any(clean == suffix.lstrip('.') or clean.endswith(suffix) for suffix in DEV_HOST_SUFFIXES)

    # findings
    def collect(self, *, production, dev=False):
        errors, warnings = [], []
        add = (lambda msg: errors.append(msg)) if production else (lambda msg: warnings.append(msg))

        secret_key = getattr(settings, 'SECRET_KEY', '') or ''
        if secret_key == DEV_SECRET_KEY:
            add('SECRET_KEY is the repository dev key — every session cookie, '
                'allauth login token and snippet preview token can be forged by '
                'anyone who reads the repo.')
        elif len(secret_key) < 50:
            warnings.append(f'SECRET_KEY is only {len(secret_key)} chars; prefer 50+ random chars.')

        if not getattr(settings, 'SECURE_SSL_REDIRECT', False):
            add('SECURE_SSL_REDIRECT is off — plaintext visits get no upgrade.')
        if int(getattr(settings, 'SECURE_HSTS_SECONDS', 0) or 0) < MIN_HSTS_SECONDS:
            add(f'SECURE_HSTS_SECONDS={settings.SECURE_HSTS_SECONDS!r} — below the {MIN_HSTS_SECONDS}s floor.')
        for name in ('SECURE_CONTENT_TYPE_NOSNIFF', 'SECURE_HSTS_INCLUDE_SUBDOMAINS'):
            if not getattr(settings, name, False):
                add(f'{name} is off.')
        for name in ('SECURE_REFERRER_POLICY', 'SECURE_CROSS_ORIGIN_OPENER_POLICY'):
            if not getattr(settings, name, None):
                add(f'{name} is unset.')
        if getattr(settings, 'X_FRAME_OPTIONS', '') != 'DENY':
            add(f"X_FRAME_OPTIONS={settings.X_FRAME_OPTIONS!r} — click-jacking needs DENY "
                "(SAMEORIGIN only arrived via the dev/preview branch).")

        if not getattr(settings, 'SESSION_COOKIE_SECURE', False) or not getattr(settings, 'CSRF_COOKIE_SECURE', False):
            add('session/CSRF cookies are not Secure — they will cross the wire in plaintext.')
        if not getattr(settings, 'SESSION_COOKIE_HTTPONLY', True):
            add('SESSION_COOKIE_HTTPONLY is off — any XSS can read the session id.')
        samesite = getattr(settings, 'SESSION_COOKIE_SAMESITE', None)
        if samesite not in ('Lax', 'Strict', 'None'):
            add(f'SESSION_COOKIE_SAMESITE={samesite!r} — expected Lax/Strict (None only for an HTTPS iframe host).')

        hosts = [h for h in (getattr(settings, 'ALLOWED_HOSTS', []) or []) if h]
        if '*' in hosts:
            add("ALLOWED_HOSTS contains '*' — Host-header poisoning and cache poisoning follow.")
        elif production and not hosts:
            add('ALLOWED_HOSTS is empty — Django only tolerates that with DEBUG on.')
        elif production and all(self._is_dev_host(h) for h in hosts):
            warnings.append(
                'every ALLOWED_HOSTS entry is a dev/preview host '
                f'({", ".join(sorted(hosts))}) — this process does not answer to a public hostname.'
            )
        origins = list(getattr(settings, 'CSRF_TRUSTED_ORIGINS', []))
        for origin in origins:
            if origin.startswith('http://'):
                add(f'CSRF_TRUSTED_ORIGINS trusts a plaintext origin: {origin}')
            elif '://' not in origin:
                add(f'CSRF_TRUSTED_ORIGINS entry {origin!r} has no scheme — it never matches.')

        if getattr(settings, 'SEED_DEMO', False):
            add('SEED_DEMO is on — the empty feed will auto-create demo accounts whose '
                'passwords are in README.md. Unset it, or use SEED_DEMO_FORCE=1 for the '
                'catalog alone.')
        if os.getenv('SEED_DEMO_FORCE', '').strip() == '1':
            add('SEED_DEMO_FORCE=1 publishes the demo catalog with no owner credentials — '
                'never set on a host that takes real uploads.')

        if getattr(settings, 'SOCIALACCOUNT_STORE_TOKENS', False):
            add('SOCIALACCOUNT_STORE_TOKENS is on — provider access tokens are being written to the DB.')
        if not getattr(settings, 'RATELIMIT_ENABLE', True):
            add('RATELIMIT_ENABLE is off — every @ratelimit decorator is currently decorative.')

        email_backend = getattr(settings, 'EMAIL_BACKEND', '')
        if production and email_backend == 'django.core.mail.backends.console.EmailBackend':
            add('EMAIL_BACKEND is the console backend — verification, password-reset, and account mail '
                'will be written to application logs instead of delivered. Configure a real SMTP/API backend.')

        # Object storage: privacy is a private bucket + signed URLs. A custom
        # domain turns every FileField.url into a public object.
        if getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', ''):
            add('AWS_S3_CUSTOM_DOMAIN is set — django-storages emits unsigned public URLs for '
                'every upload, including paid ZIPs.')
        if getattr(settings, 'AWS_ACCESS_KEY_ID', '') and not getattr(settings, 'AWS_QUERYSTRING_AUTH', True):
            add('S3 is configured with AWS_QUERYSTRING_AUTH off — objects are served unsigned.')
        if getattr(settings, 'AWS_DEFAULT_ACL', None):
            add(f"AWS_DEFAULT_ACL={settings.AWS_DEFAULT_ACL!r} — canned ACLs are how the media bucket goes public.")

        if production and dev:
            warnings.append('audited as production, but this process is a dev posture '
                            '(DEBUG=1 or DJANGO_LOCAL_DEV=1).')
        return errors, warnings
