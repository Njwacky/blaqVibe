"""
BlaqVibes email helpers — Brevo transactional email for activation & password reset.

All sending goes through Django's email backend (which is BrevoEmailBackend
when BREVO_API_KEY is set). This module provides:

- send_activation_email(user, request) — explicit wrapper used at signup
- send_password_reset_email(user, request) — not needed, Django's
  PasswordResetView already uses the backend, but kept for programmatic use
- send_generic_email(to, subject, text, html) — for trades, reviews, etc.

Why not call Brevo API directly?
- Keeps one code path: console backend in dev prints to log, Brevo in prod
  delivers. Tests can override EMAIL_BACKEND to locmem.
- Security: no duplicate secret handling, no second timeout config.

Brevo setup checklist (for operator):
  1. Create sender in Brevo dashboard (must match DEFAULT_FROM_EMAIL)
  2. Verify domain (SPF/DKIM) for deliverability
  3. Paste API key as BREVO_API_KEY env var
  4. Ensure SITE_URL is correct — activation & reset links use request.build_absolute_uri
"""
import logging
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def _site_url_from_request(request):
    if request is None:
        return getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za').rstrip('/')
    try:
        return request.build_absolute_uri('/').rstrip('/')
    except Exception:
        return getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za').rstrip('/')


def send_activation_email(user, request=None):
    """
    Send account activation email via Brevo (or console in dev).

    Returns True if queued, False otherwise.
    """
    if not user or not getattr(user, 'email', None):
        logger.warning("send_activation_email: no email for user %s", getattr(user, 'pk', None))
        return False

    try:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        # Build absolute link
        if request is not None:
            verify_path = f'/accounts/verify/{uid}/{token}/'
            link = request.build_absolute_uri(verify_path)
        else:
            base = _site_url_from_request(None)
            link = f"{base}/accounts/verify/{uid}/{token}/"

        site_url = _site_url_from_request(request)

        ctx = {
            'user': user,
            'verify_url': link,
            'site_url': site_url,
            'uid': uid,
            'token': token,
        }

        try:
            text_body = render_to_string('registration/verify_email.txt', ctx)
        except Exception:
            text_body = (
                f'Hi @{user.username},\n\n'
                f'Confirm your email to activate your BlaqVibes account:\n{link}\n\n'
                f'This link expires in 24 hours.\n\nBlaqVibes — {site_url}'
            )

        try:
            html_body = render_to_string('registration/verify_email.html', ctx)
        except Exception:
            html_body = None

        subject = 'Confirm your BlaqVibes email — activate your account'
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za')

        msg = EmailMultiAlternatives(subject, text_body, from_email, [user.email])
        if html_body:
            msg.attach_alternative(html_body, 'text/html')

        sent = msg.send(fail_silently=False)
        logger.info("activation email sent to=%s user=%s sent=%s backend=%s brevo=%s", user.email, user.username, sent, getattr(settings, 'EMAIL_BACKEND', ''), getattr(settings, 'BREVO_ENABLED', False))
        return bool(sent)
    except Exception:
        logger.exception("send_activation_email failed for user=%s", getattr(user, 'pk', None))
        return False


def send_password_reset_email_via_brevo(user, request=None, uid=None, token=None, protocol='https', domain=None):
    """
    Optional direct helper if you need to trigger password reset outside
    Django's PasswordResetView. Normally you should use the view, which
    already renders both txt and html templates and uses Brevo backend.

    This function mirrors what PasswordResetView does but explicitly uses
    our templates.
    """
    if not user or not getattr(user, 'email', None):
        return False

    try:
        if uid is None:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
        if token is None:
            token = default_token_generator.make_token(user)

        if domain is None:
            if request is not None:
                domain = request.get_host()
            else:
                # fallback to SITE_URL host
                from urllib.parse import urlparse
                parsed = urlparse(getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za'))
                domain = parsed.netloc or 'blaqvibes.co.za'

        if protocol == 'https' and getattr(settings, 'SITE_URL', '').startswith('http://'):
            protocol = 'http'

        ctx = {
            'user': user,
            'uid': uid,
            'token': token,
            'protocol': protocol,
            'domain': domain,
        }

        try:
            text_body = render_to_string('registration/password_reset_email.txt', ctx)
        except Exception:
            text_body = (
                f'Hi @{user.username},\n\n'
                f'Reset your password: {protocol}://{domain}/accounts/reset/{uid}/{token}/\n\n'
                f'If you did not request this, ignore it.\n\nBlaqVibes'
            )

        try:
            html_body = render_to_string('registration/password_reset_email.html', ctx)
        except Exception:
            html_body = None

        subject = 'Reset your BlaqVibes password'
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za')

        msg = EmailMultiAlternatives(subject, text_body, from_email, [user.email])
        if html_body:
            msg.attach_alternative(html_body, 'text/html')

        sent = msg.send(fail_silently=False)
        logger.info("password reset email sent to=%s user=%s", user.email, user.username)
        return bool(sent)
    except Exception:
        logger.exception("send_password_reset_email_via_brevo failed for %s", getattr(user, 'pk', None))
        return False


def send_generic_email(to_email, subject, text_body, html_body=None, from_email=None, tags=None):
    """
    Generic transactional email sender — used for trades, reviews, etc.
    Goes through Brevo backend when configured.
    """
    if not to_email:
        return False
    try:
        from_email = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za')
        msg = EmailMultiAlternatives(subject, text_body, from_email, [to_email])
        if html_body:
            msg.attach_alternative(html_body, 'text/html')
        sent = msg.send(fail_silently=False)
        logger.info("generic email sent to=%s subject=%r sent=%s", to_email, subject, sent)
        return bool(sent)
    except Exception:
        logger.exception("send_generic_email failed to=%s", to_email)
        return False
