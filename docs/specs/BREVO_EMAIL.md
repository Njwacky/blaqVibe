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

### "It never works" — start here

```bash
python manage.py diagnose_email --to you@example.com
```

Run this in the Render shell (or wherever the app actually runs). It walks the
whole chain and **stops at the first broken link**, so you get a specific answer
instead of "nothing arrived":

| Step | Question | Typical failure |
| ---- | -------- | --------------- |
| 1 | Is a real backend selected? | `EMAIL_BACKEND` forced to console — mail is logged, never delivered |
| 2 | Is `BREVO_API_KEY` in this process? | env var not reaching the container |
| 3 | Does Brevo accept the key? (`GET /v3/account`) | 401 — key wrong, revoked, or regenerated |
| 4 | Is `DEFAULT_FROM_EMAIL` a verified sender? (`GET /v3/senders`) | 400 — sender/domain not verified |
| 5 | Does a real send go through? | 0 credits, transactional platform not activated |

It exits non-zero on any failure, so it can gate a deploy.

Every Brevo rejection also lands in the log with the marker **`BREVO_SEND_FAILED`**,
carrying the HTTP status, Brevo's error code, the sender, and Brevo's own message.
Filter the Render log on that string.

### Other ways to test

Without real API key (dev):

```bash
DJANGO_LOCAL_DEV=1 python manage.py test users.tests.AuthAndProTests -v2
DJANGO_LOCAL_DEV=1 python manage.py test users.test_email_delivery   # failure-path coverage
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
- [ ] `python manage.py diagnose_email --to you@example.com` exits 0 on the deployed host
- [ ] Test inbox receives activation email after signup
- [ ] Test password reset flow end-to-end

## Troubleshooting

**Run `python manage.py diagnose_email --to you@example.com` first.** It names the
broken layer. Everything below is what its answers mean.

- **No email arrives, logs show 401**: API key wrong or expired. Regenerate in Brevo.
- **No email, 400 "sender not verified"**: `DEFAULT_FROM_EMAIL` must be added & verified
  in Brevo → Settings → Senders, Domains & IPs. Authenticating the whole *domain*
  covers every address on it, which is the better fix.
- **`BREVO_SEND_FAILED hint — 400 … 401 …`**: the backend could not tell which of the
  two it was; the hint line names both causes so the next step is obvious.
- **I set the env vars on Render but it still logs the console backend**: `EMAIL_BACKEND`
  is being exported too, and it *forces* the backend — it outranks `BREVO_API_KEY`.
  Delete `EMAIL_BACKEND` from the Render environment and redeploy; `settings.py`
  auto-selects Brevo when the key is present.
- **`BREVO_API_KEY is empty in this process`** even though Render shows it: the variable
  is set on the wrong service (a worker rather than the web service, or vice versa),
  the name is misspelled, or the running process predates the change — saving env vars
  in Render restarts the service, but a container already running keeps its old
  environment. `diagnose_email` reads the live process, so it is the ground truth here.
- **Emails go to spam**: Verify domain (SPF/DKIM) in Brevo, set up DMARC, avoid spammy subject.
  Note Brevo rewrites the From domain to `@brevosend.com` while your domain is
  unauthenticated — the send "succeeds" and the recipient sees a strange address.
- **Console backend in production error**: `security_check` errors when EMAIL_BACKEND is console.
  Set BREVO_API_KEY or SMTP backend.
- **Timeout**: Brevo timeout is 10s (`BREVO_TIMEOUT`), clamped to a minimum of 1s — a
  `BREVO_TIMEOUT=0` used to fail every send instantly. If Brevo is down,
  `fail_silently=True` in the signup path keeps the request alive, but the failure is
  logged at ERROR as `BREVO_SEND_FAILED`.

## Failure is never silent

Historical bug: when Brevo rejected a send, `send_verify_email` logged
`verify email queued … brevo=True` at INFO and signup told the new user
*"we sent a confirmation link to your email"*. Nothing arrived, nothing said why.

Current contract:

- `users.views.send_verify_email(request, user)` returns **True only if a backend
  accepted the message**, False otherwise.
- `gallery.views.signup` says *"we could not send the confirmation email yet"* and
  points at Settings → Email when it returns False. The account is still created.
- `users.views.edit_email` reports the failure instead of "Confirmation sent".
- The Brevo backend logs every rejection at ERROR with the `BREVO_SEND_FAILED`
  marker and raises `BrevoSendError` (carrying `status_code` / `code` / `message`)
  when `fail_silently=False`.

Covered by `users/test_email_delivery.py`.

## Future

- Use Brevo templates (templateId) for consistent branding and A/B testing
- Add tags for analytics (activation, password-reset, trade, review)
- Add webhook for bounces/spam → auto-disable bad emails
- Rate limit per recipient (already via Django ratelimit on views: 10/m for password reset, 5/h for activation resend)
