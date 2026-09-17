"""
Diagnose transactional email — answers "why is Brevo not sending?" in one run.

Usage (on the server / Render shell):
    python manage.py diagnose_email                 # config + API key + sender checks
    python manage.py diagnose_email --to me@x.com   # the above, plus a real test send

`test_brevo` sends sample mail and tells you it failed. This command walks the
whole chain and stops at the first broken link, so you get a specific answer
instead of "nothing arrived":

    1. Is an email backend actually selected?  (console = nothing is ever delivered)
    2. Is BREVO_API_KEY loaded in this process? (env var not reaching the container)
    3. Does Brevo accept that key?              (401 = wrong/expired key)
    4. Is DEFAULT_FROM_EMAIL a verified sender? (400 = sender not verified)
    5. Does a real send go through?             (credits, suspended platform)

Exits non-zero when any check fails, so it can gate a deploy.
"""
import requests
from django.conf import settings
from django.core.management.base import BaseCommand

DEFAULT_API_ROOT = "https://api.brevo.com/v3"

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def api_root():
    """Derive the API root from BREVO_API_URL so a custom endpoint is honoured."""
    url = (getattr(settings, 'BREVO_API_URL', '') or '').strip()
    if url.endswith('/smtp/email'):
        return url[: -len('/smtp/email')]
    return DEFAULT_API_ROOT


class Command(BaseCommand):
    help = "Diagnose why transactional email is not being delivered."

    def add_arguments(self, parser):
        parser.add_argument(
            '--to',
            help='Also send one real test email to this address (must be an inbox you control).',
        )
        parser.add_argument(
            '--timeout', type=int, default=15,
            help='Seconds to wait per Brevo API call (default 15).',
        )

    def handle(self, *args, **options):
        self.to = options['to']
        self.timeout = options['timeout']
        self.failures = []

        self.stdout.write(self.style.MIGRATE_HEADING('BlaqVibes email diagnosis'))
        self.stdout.write('')

        # Stop at the first broken link: a later check is meaningless (or noisy)
        # when an earlier one already failed.
        if not self.check_backend():
            return self.summary()
        if not self.check_api_key():
            return self.summary()
        self.check_sender()
        if self.to:
            self.check_live_send()
        return self.summary()

    # ------------------------------------------------------------------
    # checks
    # ------------------------------------------------------------------
    def check_backend(self):
        self.section('1. Which backend will Django use?')
        backend = getattr(settings, 'EMAIL_BACKEND', '')
        self.stdout.write(f'    EMAIL_BACKEND = {backend or "(unset)"}')

        raw_override = self._raw_env('EMAIL_BACKEND')
        if raw_override:
            self.stdout.write(f'    EMAIL_BACKEND env var is set to "{raw_override}" — this FORCES the backend')

        if 'console' in backend:
            self.fail(
                'The console backend writes mail to the application log and never delivers it. '
                'Nothing will ever arrive in an inbox on this configuration.'
            )
            if getattr(settings, 'BREVO_API_KEY', ''):
                self.stdout.write(self.style.WARNING(
                    '    BREVO_API_KEY is set but unused — EMAIL_BACKEND is overriding it. '
                    'Unset EMAIL_BACKEND so settings.py auto-selects Brevo.'
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    '    BREVO_API_KEY is empty. Get a key at https://app.brevo.com/settings/keys/api'
                ))
            return False

        if 'brevo' not in backend.lower():
            self.fail(
                f'The active backend is not Brevo ({backend or "unset"}). '
                f'This diagnostic covers the Brevo path only.'
            )
            return False

        if not getattr(settings, 'BREVO_API_KEY', ''):
            self.fail(
                'EMAIL_BACKEND is Brevo but BREVO_API_KEY is empty in this process. '
                'The env var is not reaching the container — check the deploy, not the code.'
            )
            return False

        from blaqvibes.email_backends import mask_api_key
        self.ok(f'Brevo backend selected, BREVO_API_KEY = {mask_api_key(settings.BREVO_API_KEY)}')
        self.stdout.write(f'    BREVO_API_URL = {getattr(settings, "BREVO_API_URL", "")}')
        self.stdout.write(f'    DEFAULT_FROM_EMAIL = {getattr(settings, "DEFAULT_FROM_EMAIL", "(unset)")}')
        return True

    def check_api_key(self):
        self.section('2. Does Brevo accept the API key?')
        resp = self._get('/account')
        if resp is None:
            self.fail('Could not reach the Brevo API at all — network/DNS/egress problem on this host.')
            return False
        if resp.status_code == 401:
            self.fail(
                'Brevo returned 401 Unauthorized — BREVO_API_KEY is wrong, revoked, or was '
                'regenerated after being pasted. Generate a new key at '
                'https://app.brevo.com/settings/keys/api and update the deploy.'
            )
            self.stdout.write(f'    Brevo said: {resp.text[:300]}')
            return False
        if resp.status_code != 200:
            self.fail(f'Brevo returned {resp.status_code} for GET /account: {resp.text[:300]}')
            return False

        try:
            acct = resp.json()
        except Exception:
            acct = {}
        self.ok(f'API key valid — account {acct.get("email", "(unknown)")}')
        plans = acct.get('plan') or []
        if plans:
            for p in plans:
                self.stdout.write(
                    f'    plan: {p.get("type", "?")} — {p.get("credits", "?")} credits '
                    f'({p.get("category", "")})'
                )
            # A plan with 0 transactional credits is a real "never works" cause.
            zero_credit = [
                p for p in plans
                if str(p.get('category', '')).lower() in ('classic', 'transactional')
                and p.get('credits') in (0, '0')
            ]
            if zero_credit:
                self.warn(
                    'This plan shows 0 email credits — Brevo will accept the request but '
                    'cannot deliver. Check https://app.brevo.com/account/pricing'
                )
        relay = acct.get('relay') or {}
        if relay:
            self.stdout.write(f'    transactional relay: enabled={relay.get("enabled")}')
            if relay.get('enabled') is False:
                self.fail(
                    'The transactional email platform is NOT enabled on this Brevo account. '
                    'New accounts need it activated — open a support ticket from '
                    'https://app.brevo.com/account/profile'
                )
        return True

    def check_sender(self):
        self.section('3. Is the sender address verified in Brevo?')
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '')
        if not from_email:
            self.fail('DEFAULT_FROM_EMAIL is not set — Brevo will reject every send.')
            return False

        # Strip a display name if it was configured as "BlaqVibes <noreply@x.com>".
        from email.utils import parseaddr
        _, bare = parseaddr(from_email)
        bare = bare or from_email

        resp = self._get('/senders')
        if resp is None:
            self.warn('Could not reach GET /senders — skipping the sender check.')
            return False
        if resp.status_code != 200:
            self.warn(f'GET /senders returned {resp.status_code}: {resp.text[:300]}')
            return False

        try:
            senders = resp.json().get('senders') or []
        except Exception:
            self.warn('Could not parse the /senders response.')
            return False

        if not senders:
            self.fail(
                'Your Brevo account has NO senders at all. Add one at '
                'https://app.brevo.com/settings/senders (or authenticate the whole domain '
                'under Senders, Domains & IPs) and make it match DEFAULT_FROM_EMAIL.'
            )
            return False

        emails = [str(s.get('email', '')).lower() for s in senders]
        self.stdout.write(f'    senders in Brevo: {", ".join(e for e in emails if e)}')

        if bare.lower() in emails:
            self.ok(f'DEFAULT_FROM_EMAIL {bare} is a registered Brevo sender.')
            match = next(s for s in senders if str(s.get('email', '')).lower() == bare.lower())
            if match.get('active') is False:
                self.warn('That sender is marked inactive in Brevo — reactivate it.')
            return True

        # Not a hard fail: an authenticated DOMAIN also authorises the address.
        domain = bare.split('@')[-1].lower() if '@' in bare else ''
        self.fail(
            f'DEFAULT_FROM_EMAIL is {bare} but Brevo has no sender for it. '
            f'This is the most common cause of 400 "sender not verified". '
            f'Either add {bare} as a sender, or authenticate the {domain} domain '
            f'(https://app.brevo.com/settings/senders) — then every address on it works.'
        )
        return False

    def check_live_send(self):
        self.section('4. Real test send')
        from django.core.mail import EmailMultiAlternatives

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za')
        msg = EmailMultiAlternatives(
            subject='BlaqVibes email diagnosis — this send worked',
            body='If you can read this, the Brevo transactional path is working end to end.',
            from_email=from_email,
            to=[self.to],
        )
        msg.attach_alternative(
            '<html><body style="font-family:sans-serif;padding:24px">'
            '<h2>BlaqVibes email diagnosis</h2>'
            '<p>If you can read this, the Brevo transactional path is working end to end.</p>'
            '</body></html>',
            'text/html',
        )
        try:
            n = msg.send(fail_silently=False)
        except Exception as exc:
            detail = getattr(exc, 'message', None) or str(exc)
            status = getattr(exc, 'status_code', '')
            self.fail(f'Brevo rejected the send (HTTP {status}): {detail}')
            self.stdout.write(self.style.WARNING(
                '    Full Brevo error body is in the log line marked BREVO_SEND_FAILED.'
            ))
            return False
        if not n:
            self.fail('The backend reported 0 messages sent.')
            return False
        self.ok(f'Sent to {self.to}. Check the inbox AND spam folder, then Brevo → '
                'Transactional → Emails for the delivery/bounce event.')
        return True

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _get(self, path):
        try:
            return requests.get(
                api_root() + path,
                headers={'api-key': getattr(settings, 'BREVO_API_KEY', ''), 'Accept': 'application/json'},
                timeout=self.timeout,
            )
        except Exception as exc:
            self.stdout.write(f'    request error: {type(exc).__name__}: {exc}')
            return None

    @staticmethod
    def _raw_env(name):
        import os
        return os.getenv(name, '').strip()

    def section(self, title):
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(title))

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS(f'  [{OK}] {text}'))

    def warn(self, text):
        self.stdout.write(self.style.WARNING(f'  [{WARN}] {text}'))

    def fail(self, text):
        self.stdout.write(self.style.ERROR(f'  [{FAIL}] {text}'))
        self.failures.append(text)

    def summary(self):
        self.stdout.write('')
        if self.failures:
            self.stdout.write(self.style.ERROR(
                f'{len(self.failures)} problem(s) found — fix the first one, then re-run this command.'
            ))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(
            'Every check passed. If mail still does not arrive, it is a deliverability '
            'issue (SPF/DKIM/DMARC or spam folder), not a configuration one — see '
            'docs/specs/BREVO_EMAIL.md → Troubleshooting.'
        ))
