"""
Admin approval notifications — ensure admins/moderators get notified when
someone wants approval.

Why this module?
- Before: `moderators_to_notify()` only created in-app Notification rows.
  Admins had to manually open /admin/dashboard/ or /reports/ to see pending work.
  No email → approvals sat for hours.
- After: Every approval-needing event creates BOTH:
  1. In-app Notification (existing `notify()` path)
  2. Email via Brevo (transactional API) to all moderators/admins/superadmins

Recipients are BOTH staff axes (get_admin_users_for_notification): the app
role axis (profile.role moderator/admin/superadmin) AND the Django axis
(is_staff/is_superuser). A fresh install whose only operator came from
`createsuperuser` used to match NEITHER filter — uploads arrived and nobody
was ever told. Repeat fan-outs for the SAME decision are deduped while the
first row is unread (dedupe_body_prefix), and resolved projects clear their
own stale rows (dismiss_admin_project_notifications).

Events covered:
  - ZIP upload needs review (new user <3 published, or scanner offline)
  - Project quarantined (virus / secrets)
  - New report filed against a vibe
  - Challenge drafts created (AI weekly drafts, is_active=False)
  - Generic approval request (future: role requests, etc.)

Uses Brevo backend when BREVO_API_KEY set, else falls back to console/locmem
(Django's EMAIL_BACKEND). Respects fail_silently so a dead email host never
blocks the request.

Security:
- No secrets in email body (filenames only, never secret values)
- Email list is moderators/admins/superadmins with verified email addresses
- Individual emails (not one big To) to avoid leaking admin list
- Rate-limited at view layer (existing @ratelimit), not here — this is the
  fan-out, not the gate.
"""
import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.db.models import Q

logger = logging.getLogger(__name__)


def get_admin_users_for_notification():
    """
    Everyone who should open their inbox when an approval is needed.

    Two axes, both included:
      1. The app role axis — profile.role in moderator/admin/superadmin
         (the people who work /moderation/queue/).
      2. The Django axis — is_staff / is_superuser. A fresh install's
         `createsuperuser` account has profile.role='user', and before this
         account was included the fan-out found NOBODY: uploads arrived and
         no staff member ever saw a notification. Anyone holding Django
         staff keys can already read every project in /blaq-admin-secure/,
         so telling them about pending work leaks nothing.

    Ordered by username for stable logs.
    """
    try:
        from django.contrib.auth.models import User
        # NOTE: no email filter here. The in-app row must reach staff even
        # when they never set an email (a `createsuperuser` account can skip
        # it) — emails go only to the subset that HAS one (get_admin_emails /
        # notify_admins_for_approval filter per recipient).
        return (
            User.objects.filter(is_active=True)
            .filter(
                Q(profile__role='moderator') | Q(profile__role='admin')
                | Q(profile__role='superadmin')
                | Q(is_staff=True) | Q(is_superuser=True)
            )
            .order_by('username')
            .select_related('profile')
            .distinct()
        )
    except Exception:
        logger.exception('get_admin_users_for_notification failed')
        return []


def get_admin_emails():
    """List of email strings for admins who have an email set."""
    try:
        return [u.email for u in get_admin_users_for_notification() if u.email]
    except Exception:
        return []


def _render_email_templates(txt_template, html_template, context):
    """Render txt + html templates with fallback to simple text."""
    text_body = None
    html_body = None

    if txt_template:
        try:
            text_body = render_to_string(txt_template, context)
        except Exception as e:
            logger.debug('txt template %s render failed: %s', txt_template, e)

    if html_template:
        try:
            html_body = render_to_string(html_template, context)
        except Exception as e:
            logger.debug('html template %s render failed: %s', html_template, e)

    return text_body, html_body


def send_admin_email(subject, text_body, html_body=None, to_emails=None, context=None, tags=None):
    """
    Send email to admin list via Brevo (or console in dev).

    - to_emails: explicit list, else all admin emails
    - subject: email subject
    - text_body / html_body: rendered bodies
    - tags: Brevo tags for dashboard filtering (e.g. ['approval', 'report'])
    Returns number of emails sent.
    """
    if to_emails is None:
        to_emails = get_admin_emails()

    if not to_emails:
        logger.info('send_admin_email: no admin emails found for subject=%r', subject)
        return 0

    # Ensure we have at least text
    if not text_body and not html_body:
        logger.warning('send_admin_email: no body for subject=%r', subject)
        return 0

    if not text_body and html_body:
        # Fallback: strip html tags crudely for text part (Brevo needs textContent)
        import re
        text_body = re.sub('<[^<]+?>', '', html_body)

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za')

    sent = 0
    for email in to_emails:
        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body or '',
                from_email=from_email,
                to=[email],
            )
            if html_body:
                msg.attach_alternative(html_body, 'text/html')

            # Brevo backend will pick up tags from subject? We set via payload building
            # For generic backend, tags are ignored — fine.
            # We attach tags via header-like trick: Brevo backend reads subject for tags,
            # but we also support extra_headers if needed in future.

            msg.send(fail_silently=True)
            sent += 1
            logger.info('admin email sent to=%s subject=%r tags=%s', email, subject, tags)
        except Exception:
            logger.exception('admin email failed to=%s subject=%r', email, subject)

    return sent


def notify_admins_for_approval(kind, title, body, url, email_subject=None, email_text_template=None, email_html_template=None, context=None, project=None, report=None, dedupe_body_prefix=None):
    """
    Unified admin notification for approval-needing events.

    Creates in-app Notification for each admin AND sends Brevo email.

    kind: Notification kind — 'approval', 'pending', 'review_needed', 'report', 'upload',
          'quarantined', 'challenge', 'account_quarantine' (a person, not a project), 'appeal'
    title: in-app title (short)
    body: in-app body (short)
    url: link to action (e.g. project.get_absolute_url() or /moderation/)
    email_subject: email subject (defaults to title)
    email_text_template / email_html_template: templates to render for email
    context: extra context for email templates
    project: optional AppProject for context
    report: optional AppReport for context
    dedupe_body_prefix: optional. When set, an admin who ALREADY has an UNREAD
        row of the same kind+title whose body starts with this prefix gets
        neither a second row nor a second email. One upload fans out at least
        twice (queue entry, then the scan verdict) and the decision waiting on
        staff is the SAME — duplicate rows and emails per fan-out turn the
        inbox into noise and train staff to ignore it. Once the row is read
        the next fan-out notifies again (a re-escalation, on purpose).
    """
    from .notify import notify
    from .reports import moderators_to_notify

    # Determine admins to notify (exclude reporter if needed — caller handles)
    # For generic approval, use all admins
    try:
        admins = list(get_admin_users_for_notification())
        if not admins:
            # A fan-out with no recipients is the exact failure behind "staff
            # never see uploads" — loud, not informational.
            logger.warning('notify_admins_for_approval: NO staff found to notify kind=%s title=%r — '
                           'check that at least one active account has a moderator role or Django staff flags',
                           kind, title)
            return 0

        # In-app notifications
        in_app_count = 0
        fresh_admins = []  # admins who got a new row (or a failed create) → still email them
        for admin in admins:
            try:
                if dedupe_body_prefix:
                    from .models import Notification
                    already_open = Notification.objects.filter(
                        user=admin, kind=kind, title=title[:200], is_read=False,
                        body__startswith=dedupe_body_prefix[:400],
                    ).exists()
                    if already_open:
                        continue  # the same decision is already waiting in their inbox
                # Avoid notifying the actor if they are admin themselves (optional)
                # For now, notify all — admin action by admin still shows in their own inbox for audit
                n = notify(admin, kind, title, body, url)
                if n:
                    in_app_count += 1
                # Email even when the in-app row failed — the email is the
                # backup channel, so a DB hiccup must not silence the alert.
                fresh_admins.append(admin)
            except Exception:
                logger.exception('in-app notify failed for admin %s', getattr(admin, 'username', '?'))
                fresh_admins.append(admin)

        # Email notifications
        # Build email context
        email_ctx = {
            'title': title,
            'body': body,
            'url': url,
            'site_url': getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za').rstrip('/'),
            'project': project,
            'report': report,
            'admin_count': len(admins),
        }
        if context:
            email_ctx.update(context)

        # Render email bodies if templates provided, else use title/body
        text_body = None
        html_body = None
        if email_text_template or email_html_template:
            text_body, html_body = _render_email_templates(email_text_template, email_html_template, email_ctx)

        if not text_body:
            # Fallback simple text
            text_body = (
                f"{title}\n\n"
                f"{body}\n\n"
                f"Action: {email_ctx['site_url']}{url}\n\n"
                f"— BlaqVibes Admin Alerts\n"
                f"This email was sent to all moderators/admins because an approval is needed."
            )

        if not html_body and email_html_template is None:
            # Generate minimal branded HTML fallback. Title/body are user-shaped
            # text (project titles, report details) — escape them so a title
            # like `<img src=x onerror=...>` stays text in the mail client.
            from django.utils.html import escape
            safe_title, safe_body = escape(title), escape(body)
            html_body = (
                f"<html><body style=\"font-family:Inter,Helvetica,Arial,sans-serif;background:#0a0a0f;padding:24px;color:#ddd;\">"
                f"<div style=\"max-width:600px;margin:0 auto;background:#11111a;border:1px solid #222;border-radius:12px;padding:24px;\">"
                f"<div style=\"display:inline-block;background:#7c3aed;color:#fff;font-weight:800;padding:6px 12px;border-radius:8px;font-size:13px;margin-bottom:12px;\">ADMIN • APPROVAL NEEDED</div>"
                f"<h2 style=\"margin:0 0 8px 0;color:#fff;font-size:18px;\">{safe_title}</h2>"
                f"<p style=\"color:#aaa;line-height:1.6;font-size:14px;\">{safe_body}</p>"
                f"<p style=\"margin:20px 0 0 0;\"><a href=\"{email_ctx['site_url']}{url}\" style=\"display:inline-block;background:#7c3aed;color:#fff;text-decoration:none;font-weight:600;padding:10px 18px;border-radius:8px;\">Review & Approve</a></p>"
                f"<p style=\"color:#666;font-size:12px;margin-top:20px;\">Sent to all moderators/admins • {email_ctx['site_url']}</p>"
                f"</div></body></html>"
            )

        email_subject_final = email_subject or f"[BlaqVibes Admin] {title}"

        # Determine tags for Brevo dashboard
        tags = ['admin', 'approval']
        if kind in ('report', 'quarantined', 'upload', 'pending', 'review_needed', 'challenge',
                    'account_quarantine', 'appeal'):
            tags.append(kind)

        # Only admins who got a FRESH decision this time get the email —
        # dedupe-skipped admins already have the same decision in their inbox.
        email_sent = send_admin_email(
            subject=email_subject_final,
            text_body=text_body,
            html_body=html_body,
            to_emails=[u.email for u in fresh_admins if u.email],
            context=email_ctx,
            tags=tags,
        )

        logger.info(
            'admin approval notification kind=%s title=%r staff=%s in_app=%s email=%s url=%s',
            kind, title, len(admins), in_app_count, email_sent, url,
        )
        return in_app_count + email_sent

    except Exception:
        logger.exception('notify_admins_for_approval failed kind=%s title=%r', kind, title)
        return 0


# Specific helpers for common approval events

def notify_admins_pending_project(project, reason=""):
    """Project needs manual approval (new user, or scanner offline)."""
    title = f'Pending approval: {project.title}'
    body = f'@{project.owner.username} uploaded "{project.title}" — {reason or "requires manual approval"}'
    url = project.get_absolute_url() if hasattr(project, 'get_absolute_url') else f'/app/{project.slug}/'
    # Use moderation queue URL for admin action
    admin_url = '/moderation/queue/'

    return notify_admins_for_approval(
        kind='approval',
        title=title,
        body=body,
        url=admin_url,
        email_subject=f'[Approval Needed] {project.title} by @{project.owner.username}',
        email_text_template='emails/admin_pending_approval.txt',
        email_html_template='emails/admin_pending_approval.html',
        context={
            'project': project,
            'reason': reason,
            'owner': project.owner,
            'project_url': url,
            'moderation_url': admin_url,
        },
        project=project,
        # One upload fans out here at queue-entry AND again from the scan
        # verdict — same decision, so the second call stays quiet while the
        # first row is still unread.
        dedupe_body_prefix=f'@{project.owner.username} uploaded "{project.title}"',
    )


def dismiss_admin_project_notifications(project, kinds=('approval', 'upload', 'quarantined')):
    """
    Mark staff inbox rows about this project as read once its fate is decided.

    A "Pending approval" row that survives the project's auto-publish (or a
    human approve/reject) is stale noise — it keeps the badge lit for a
    decision that no longer exists, which is exactly how staff learn to
    ignore the badge. Called from the pipeline's publish step and from
    moderation_action, so every resolution path clears its own paper trail.

    Rows are matched on the body prefix this module writes (stable across
    reasons), never on user-supplied text patterns.
    """
    try:
        from .models import Notification
        owner = getattr(project, 'owner', None)
        if owner is None:
            return 0
        upload_prefix = f'@{owner.username} uploaded "{project.title}"'
        quarantine_prefix = f'"{project.title}" by @{owner.username} was quarantined'
        from django.db.models import Q
        updated = Notification.objects.filter(
            kind__in=list(kinds), is_read=False,
        ).filter(
            Q(body__startswith=upload_prefix) | Q(body__startswith=quarantine_prefix)
        ).update(is_read=True)
        if updated:
            logger.info('dismissed %s stale staff notification(s) for %s', updated, project.slug)
        return updated
    except Exception:
        logger.exception('dismiss_admin_project_notifications failed slug=%s', getattr(project, 'slug', '?'))
        return 0


def notify_admins_quarantined_project(project, reason="", secrets=None):
    """Project quarantined (virus / secrets) — needs admin review."""
    title = f'Quarantined: {project.title}'
    body = f'"{project.title}" by @{project.owner.username} was quarantined — {reason or "virus or secrets detected"}'
    admin_url = '/moderation/queue/'

    return notify_admins_for_approval(
        kind='quarantined',
        title=title,
        body=body,
        url=admin_url,
        email_subject=f'[Quarantined] {project.title} needs review',
        email_text_template='emails/admin_quarantined.txt',
        email_html_template='emails/admin_quarantined.html',
        context={
            'project': project,
            'reason': reason,
            'secrets': secrets or [],
            'owner': project.owner,
            'moderation_url': admin_url,
        },
        project=project,
        dedupe_body_prefix=f'"{project.title}" by @{project.owner.username} was quarantined',
    )


def notify_admins_new_report(report):
    """New report filed — needs triage."""
    try:
        project = report.project
        title = f'New report: {project.title} — {report.get_reason_display()}'
        body = f'Reported by @{report.user.username if report.user else "anonymous"}: {report.details[:120] or report.get_reason_display()}'
        admin_url = '/moderation/reports/'

        return notify_admins_for_approval(
            kind='report',
            title=title,
            body=body,
            url=admin_url,
            email_subject=f'[Report] {project.title} — {report.get_reason_display()}',
            email_text_template='emails/admin_new_report.txt',
            email_html_template='emails/admin_new_report.html',
            context={
                'report': report,
                'project': project,
                'reporter': report.user,
                'reports_url': admin_url,
            },
            project=project,
            report=report,
        )
    except Exception:
        logger.exception('notify_admins_new_report failed report_id=%s', getattr(report, 'pk', None))
        return 0


def notify_admins_challenge_drafts(challenges):
    """AI drafted challenges — superadmin must approve."""
    if not challenges:
        return 0
    try:
        count = len(challenges)
        title = f'{count} draft challenges ready for approval'
        body = f'AI drafted {count} challenges — {", ".join(c.tag for c in challenges[:3])}{"..." if count > 3 else ""}'
        admin_url = '/challenges/'  # drafts visible in /admin/dashboard/ and /challenges/?drafts

        return notify_admins_for_approval(
            kind='challenge',
            title=title,
            body=body,
            url=admin_url,
            email_subject=f'[Challenges] {count} draft challenges need approval',
            email_text_template='emails/admin_challenge_drafts.txt',
            email_html_template='emails/admin_challenge_drafts.html',
            context={
                'challenges': challenges,
                'count': count,
                'challenges_url': admin_url,
            },
        )
    except Exception:
        logger.exception('notify_admins_challenge_drafts failed')
        return 0


def notify_admins_new_user(user):
    """New user signed up — informational, not blocking, but admin sees growth."""
    try:
        title = f'New user: @{user.username}'
        body = f'@{user.username} ({user.email}) just joined BlaqVibes'
        admin_url = '/admin/dashboard/'

        return notify_admins_for_approval(
            kind='approval',
            title=title,
            body=body,
            url=admin_url,
            email_subject=f'[New User] @{user.username} joined',
            email_text_template=None,
            email_html_template=None,
            context={'new_user': user},
        )
    except Exception:
        logger.exception('notify_admins_new_user failed user=%s', getattr(user, 'pk', None))
        return 0
