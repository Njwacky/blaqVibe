# Brevo Transactional Email — Activation & Password Reset

BlaqVibes uses Brevo (formerly Sendinblue) for transactional email:
- Account activation (confirm email → 5★ welcome grant)
- Password reset (Django PasswordResetView)
- Security alerts (new device sign-in)
- Trades, reviews, tips (future: can be upgraded to HTML)

## Why Brevo?

- Console backend is dev-only: emails go to logs, never to inbox.
- SMTP (port 587) is often blocked on PaaS and needs TLS config.
- Brevo API is HTTPS (port 443), works behind any proxy, no open port.
- Dashboard gives delivery tracking, bounces, spam complaints.

## Setup

1. Create Brevo account: https://app.brevo.com/
2. Create API key: https://app.brevo.com/settings/keys/api
   - Scope: transactional emails (default)
   - Key starts with `xkeysib-`
3. Verify sender domain & email:
   - https://app.brevo.com/settings/senders
   - Add `noreply@blaqvibes.co.za` (or your DEFAULT_FROM_EMAIL)
   - Verify domain SPF/DKIM for deliverability (important!)
4. Set env vars:

```bash
BREVO_API_KEY=xkeysib-...your-key...
BREVO_API_URL=https://api.brevo.com/v3/smtp/email  # default, can omit
BREVO_SENDER_NAME=BlaqVibes                         # default, can omit
DEFAULT_FROM_EMAIL=noreply@blaqvibes.co.za          # must be verified in Brevo
SITE_URL=https://blaqvibes.co.za
```

5. Deploy — settings.py auto-selects Brevo backend when BREVO_API_KEY is present:

```python
if BREVO_API_KEY:
    EMAIL_BACKEND = 'blaqvibes.email_backends.BrevoEmailBackend'
```

You can force override with `EMAIL_BACKEND` env var if needed.

## How it works

### Backend: `blaqvibes/email_backends.py`

Implements `django.core.mail.backends.base.BaseEmailBackend`:

- `send_messages()` loops over EmailMessage / EmailMultiAlternatives
- Builds Brevo payload:
  ```json
  {
    "sender": {"email": "noreply@blaqvibes.co.za", "name": "BlaqVibes"},
    "to": [{"email": "user@example.com"}],
    "subject": "...",
    "textContent": "plain text",
    "htmlContent": "<html>...",
    "tags": ["activation"]  // for dashboard filtering
  }
  ```
- POST to `https://api.brevo.com/v3/smtp/email` with header `api-key: <key>`
- 10s timeout, logs error body, respects `fail_silently`

Handles:
- `from_email` parsing (`Name <email>` or plain)
- `to`, `cc`, `bcc`
- `reply_to`
- `content_subtype == 'html'` → htmlContent
- `alternatives` → htmlContent wins
- text-only → auto-generates minimal HTML for better inbox placement

### Activation flow

1. User signs up at `/accounts/signup/` → `gallery.views.signup`
2. `users.views.send_verify_email(request, user)` called:
   - Generates uid + token via `default_token_generator`
   - Builds absolute link via `request.build_absolute_uri`
   - Renders:
     - `templates/registration/verify_email.txt`
     - `templates/registration/verify_email.html` (new)
   - Sends `EmailMultiAlternatives` with both → Brevo backend posts both textContent & htmlContent
3. User clicks link → `/accounts/verify/<uid>/<token>/` → marks `profile.email_verified=True` and grants 5★

Helper also in `users/emails.py`: `send_activation_email(user, request)` for programmatic use.

### Password reset flow

Django's built-in `PasswordResetView`:

- URL: `/accounts/password_reset/` → `blaqvibes/urls.py`
- Configured with:
  ```python
  email_template_name='registration/password_reset_email.txt'
  html_email_template_name='registration/password_reset_email.html'  # new
  subject_template_name='registration/password_reset_subject.txt'
  ```
- Renders both templates with context: `user, uid, token, protocol, domain`
- Sends via Brevo backend (same payload building)

Link: `/accounts/reset/<uidb64>/<token>/` → `AccountPasswordResetConfirmView` which revokes sessions & git tokens after reset.

### Security alerts

`users/security.py:record_login` sends new-device email via `EmailMultiAlternatives` with HTML, also Brevo-ready.

## Templates

New / updated:

- `templates/registration/verify_email.txt` — plain text activation
- `templates/registration/verify_email.html` — branded HTML (dark, violet gradient, CTA button, perks, fallback link)
- `templates/registration/password_reset_email.txt` — improved text
- `templates/registration/password_reset_email.html` — branded HTML (red CTA, security tips)

All templates use `{{ verify_url }}` or `{{ protocol }}://{{ domain }}{% url ... %}` and are safe (no user input in HTML, only username/email).

## Testing

Without real API key (dev):

```bash
DJANGO_LOCAL_DEV=1 python manage.py test users.tests.AuthAndProTests -v2
```

Emails go to console or locmem backend, visible in logs.

With Brevo key — dry run payload:

```bash
python manage.py test_brevo --to you@example.com --dry-run
```

Real send (to verified inbox):

```bash
BREVO_API_KEY=xkeysib-... python manage.py test_brevo --to you@example.com
```

This sends 3 samples: activation, reset, generic.

## Production checklist

- [ ] BREVO_API_KEY set in Render / env
- [ ] DEFAULT_FROM_EMAIL verified in Brevo dashboard
- [ ] Domain SPF/DKIM verified (Brevo → Settings → Domains)
- [ ] SITE_URL correct (links use request host, but fallback uses SITE_URL)
- [ ] `python manage.py security_check --as-production` passes (no console backend error)
- [ ] Test inbox receives activation email after signup
- [ ] Test password reset flow end-to-end

## Troubleshooting

- **No email arrives, logs show 401**: API key wrong or expired. Regenerate in Brevo.
- **No email, 400 "sender not verified"**: DEFAULT_FROM_EMAIL must be added & verified in Brevo → Senders.
- **Emails go to spam**: Verify domain (SPF/DKIM) in Brevo, set up DMARC, avoid spammy subject.
- **Console backend in production error**: `security_check` errors when EMAIL_BACKEND is console. Set BREVO_API_KEY or SMTP backend.
- **Timeout**: Brevo timeout is 10s (BREVO_TIMEOUT). If Brevo is down, `fail_silently=True` in most paths keeps request alive, but logs error.

## Future

- Use Brevo templates (templateId) for consistent branding and A/B testing
- Add tags for analytics (activation, password-reset, trade, review)
- Add webhook for bounces/spam → auto-disable bad emails
- Rate limit per recipient (already via Django ratelimit on views: 10/m for password reset, 5/h for activation resend)
