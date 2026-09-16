"""
BlaqVibes Brevo transactional email backend.

Uses Brevo (formerly Sendinblue) SMTP API:
  POST https://api.brevo.com/v3/smtp/email
  Header: api-key: <BREVO_API_KEY>

Why a custom backend?
- Console backend is dev-only: verification & password-reset never arrive.
- SMTP can be flaky on PaaS (port 587 blocked, TLS issues).
- Brevo API is HTTP, no open port, works behind any proxy, and gives
  delivery tracking in the Brevo dashboard.

The backend accepts any Django EmailMessage / EmailMultiAlternatives:
  * subject, from_email, to, cc, bcc, reply_to
  * body as text/plain (or text/html when content_subtype == 'html')
  * alternatives: text/html wins for htmlContent

Fail-safe:
- If BREVO_API_KEY missing, raises or returns 0 depending on fail_silently.
- 10s timeout so a dead Brevo host never parks a request thread.
- Logs full error body at ERROR level for ops.
"""
import html as html_lib
import logging
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

BREVO_DEFAULT_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoEmailBackend(BaseEmailBackend):
    """
    Django email backend that sends via Brevo Transactional API.

    Required settings:
      BREVO_API_KEY - your Brevo API v3 key (xkeysib-...)

    Optional settings:
      BREVO_API_URL - override endpoint (default: https://api.brevo.com/v3/smtp/email)
      BREVO_SENDER_NAME - default sender display name (default: BlaqVibes)
      DEFAULT_FROM_EMAIL - sender email address
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_key = getattr(settings, "BREVO_API_KEY", "") or ""
        self.api_url = getattr(settings, "BREVO_API_URL", BREVO_DEFAULT_URL) or BREVO_DEFAULT_URL
        self.sender_name = getattr(settings, "BREVO_SENDER_NAME", "BlaqVibes") or "BlaqVibes"
        self.timeout = getattr(settings, "BREVO_TIMEOUT", 10)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        if not self.api_key:
            msg = "BREVO_API_KEY not configured — cannot send email via Brevo"
            logger.error(msg)
            if not self.fail_silently:
                raise ValueError(msg)
            return 0

        sent = 0
        for message in email_messages:
            try:
                payload = self._build_payload(message)
                if not payload.get("to"):
                    logger.warning("Brevo skip: no valid recipient in message %r", message.subject)
                    continue

                resp = requests.post(
                    self.api_url,
                    headers={
                        "api-key": self.api_key,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )

                if resp.status_code in (200, 201, 202):
                    sent += 1
                    logger.info(
                        "Brevo sent: subject=%r to=%r messageId=%s",
                        message.subject,
                        [t.get("email") for t in payload.get("to", [])],
                        resp.json().get("messageId") if resp.content else "-",
                    )
                else:
                    logger.error(
                        "Brevo send failed: status=%s body=%s subject=%r to=%r",
                        resp.status_code,
                        resp.text[:1000],
                        message.subject,
                        payload.get("to"),
                    )
                    if not self.fail_silently:
                        resp.raise_for_status()
            except Exception as exc:
                logger.exception("Brevo send exception for %r: %s", getattr(message, "subject", ""), exc)
                if not self.fail_silently:
                    raise
        return sent

    def _build_payload(self, message):
        # --- sender ---
        from_name, from_email = parseaddr(message.from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@blaqvibes.co.za"))
        if not from_email:
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@blaqvibes.co.za")
        if not from_name:
            from_name = self.sender_name

        # --- recipients ---
        def _parse_list(addrs):
            out = []
            for addr in addrs or []:
                name, email = parseaddr(addr)
                if not email:
                    continue
                if name:
                    out.append({"email": email, "name": name})
                else:
                    out.append({"email": email})
            return out

        to_list = _parse_list(message.to)
        cc_list = _parse_list(getattr(message, "cc", None))
        bcc_list = _parse_list(getattr(message, "bcc", None))

        # --- content ---
        text_content = None
        html_content = None

        # content_subtype handling
        if getattr(message, "content_subtype", "plain") == "html":
            html_content = message.body
        else:
            text_content = message.body

        # alternatives: Django EmailMultiAlternatives stores (content, mimetype)
        for alt_content, mimetype in getattr(message, "alternatives", []):
            if mimetype == "text/html":
                html_content = alt_content
            elif mimetype == "text/plain" and not text_content:
                text_content = alt_content

        # Fallbacks
        if not text_content and not html_content:
            text_content = message.body or ""

        # If only text, generate minimal html for better inbox rendering
        if text_content and not html_content:
            escaped = html_lib.escape(text_content)
            # preserve line breaks visually, keep safe
            html_content = (
                "<html><body style=\"font-family:Inter,Helvetica,Arial,sans-serif;"
                "color:#111;line-height:1.6;padding:24px;max-width:600px;margin:0 auto;\">"
                f"<pre style=\"font-family:inherit;white-space:pre-wrap;word-wrap:break-word;\">{escaped}</pre>"
                "</body></html>"
            )

        payload = {
            "sender": {"email": from_email, "name": from_name},
            "to": to_list,
            "subject": message.subject or "(no subject)",
        }
        if text_content:
            payload["textContent"] = text_content
        if html_content:
            payload["htmlContent"] = html_content
        if cc_list:
            payload["cc"] = cc_list
        if bcc_list:
            payload["bcc"] = bcc_list

        # reply-to — Brevo expects single replyTo object
        reply_to = getattr(message, "reply_to", None)
        if reply_to:
            rt_name, rt_email = parseaddr(reply_to[0])
            if rt_email:
                payload["replyTo"] = {"email": rt_email, "name": rt_name} if rt_name else {"email": rt_email}

        # tags for Brevo dashboard filtering (optional but useful)
        # We tag by purpose when subject hints at it
        subj_lower = (message.subject or "").lower()
        tags = []
        if "confirm" in subj_lower or "verify" in subj_lower or "activate" in subj_lower:
            tags.append("activation")
        if "reset" in subj_lower or "password" in subj_lower:
            tags.append("password-reset")
        if "trade" in subj_lower or "tipped" in subj_lower or "review" in subj_lower:
            tags.append("notification")
        if tags:
            payload["tags"] = tags

        return payload
