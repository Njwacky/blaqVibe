"""Feedback conversations — the temporary fast path to a human.

Why this module exists
- The product is still under construction, so the fastest way for a builder
  to reach a real person is the glowing floating button on every page
  (.bv-fab in the base template). It opens /feedback/ — a thread, not a
  mailbox: the user writes here, a superadmin reads it in their inbox and
  replies into the SAME thread, and the user sees the reply both in this
  conversation and in their regular inbox.
- Every user message lands in the superadmin's inbox: one in-app
  Notification row per superadmin (gallery.notify, kind 'feedback') plus
  one email each via the Brevo backend (fail-silent, same discipline as
  gallery.admin_notifications). A dead mail host must never 500 the request.
- Only role 'superadmin' may open the queue (/admin/feedback/) or reply.
  The glowing button promises one pair of human eyes, so this channel
  stays personal rather than becoming a support desk.

Security notes
- Users only ever see or write THEIR OWN threads: get_object_or_404 is
  filtered by owner and there is no other read path (the Django admin
  registration is read-only).
- Writes are rate-limited per user (django_ratelimit), bodies are capped
  at 4000 chars server-side (maxlength alone is a lie), and optional
  screenshots are byte/format/pixel checked before storage. The same
  profanity gate the inbox uses applies inside gallery.notify.notify.
"""
import logging
import mimetypes

from PIL import Image, UnidentifiedImageError

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit

from .decorators import superadmin_required
from .models import FeedbackMessage, FeedbackThread

logger = logging.getLogger(__name__)

# The caps are enforced HERE, not by HTML attributes. The browser's file
# picker is only a convenience; the server trusts neither its MIME type nor
# its filename.
BODY_MAX = 4000
ATTACHMENT_MAX_BYTES = 7 * 1024 * 1024
ATTACHMENT_MAX_PIXELS = 40_000_000
ALLOWED_IMAGE_FORMATS = frozenset({'PNG', 'JPEG', 'WEBP'})
IMAGE_FORMAT_EXTENSIONS = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}


def _validated_attachment(request):
    """Return (upload, error) for the optional screenshot in a POST.

    Pillow inspects the bytes, not just ``Content-Type``. This keeps renamed
    HTML/SVG/files with dangerous payloads out of the feedback media store,
    while the size and pixel caps stop a huge image from becoming a memory
    denial of service.
    """
    upload = request.FILES.get('attachment')
    if not upload:
        return None, ''
    if upload.size > ATTACHMENT_MAX_BYTES:
        return None, 'That picture is too large. Screenshots must be 7MB or smaller.'
    try:
        with Image.open(upload) as image:
            image_format = (image.format or '').upper()
            width, height = image.size
            if image_format not in ALLOWED_IMAGE_FORMATS:
                return None, 'Attach a PNG, JPEG, or WebP image.'
            if width <= 0 or height <= 0 or width * height > ATTACHMENT_MAX_PIXELS:
                return None, 'That picture is too large in dimensions. Please attach a smaller screenshot.'
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None, 'We could not read that picture. Try exporting the screenshot as PNG or JPEG.'
    finally:
        try:
            upload.seek(0)
        except Exception:
            pass
    # Canonicalise the extension from the bytes, not the browser-provided
    # filename, so the protected response gets the correct image MIME type.
    upload.name = f'feedback-screenshot{IMAGE_FORMAT_EXTENSIONS[image_format]}'
    return upload, ''


def _message_input(request, empty_message):
    """Read a feedback body plus optional screenshot with one server contract."""
    body = (request.POST.get('body') or '').strip()[:BODY_MAX]
    attachment, error = _validated_attachment(request)
    if error:
        return body, None, error
    if not body and not attachment:
        return body, None, empty_message
    # An image-only report is useful — keep a readable message in the thread
    # so previews, notifications and email never look empty.
    if attachment and not body:
        body = 'Screenshot attached.'
    return body, attachment, ''


# ---------------------------------------------------------------------
# Fan-out: user -> superadmin inbox, superadmin -> user inbox.
# ---------------------------------------------------------------------

def _superadmins():
    from django.contrib.auth.models import User
    return (
        User.objects.filter(is_active=True)
        .filter(profile__role='superadmin')
        .order_by('username')
        .select_related('profile')
    )


def notify_superadmins_of_feedback(thread, follow_up=False):
    """Put a fresh user message in EVERY superadmin's inbox.

    In-app row per superadmin (kind 'feedback', linking to the admin
    conversation) + one email each (Brevo backend; console/locmem in
    tests). Fail-silent: returns the in-app count, never raises.
    """
    from gallery.admin_notifications import send_admin_email
    from gallery.notify import notify

    try:
        latest = thread.messages.order_by('-created_at', '-id').first()
        snippet = (latest.body if latest else '').strip()[:180]
        if latest and latest.attachment and 'screenshot attached' not in snippet.lower():
            snippet = f'{snippet} · Screenshot attached' if snippet else 'Screenshot attached'
        title = (
            f'@{thread.user.username} followed up on their feedback'
            if follow_up
            else f'New feedback from @{thread.user.username}'
        )
        url = f'/admin/feedback/{thread.pk}/'

        count = 0
        supers = list(_superadmins())
        for sa in supers:
            try:
                if notify(sa, 'feedback', title, snippet, url):
                    count += 1
            except Exception:
                logger.exception('feedback in-app notify failed for %s', sa.username)

        emails = [u.email for u in supers if u.email]
        if emails:
            site = getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za').rstrip('/')
            email_ctx = {
                'title': title,
                'username': thread.user.username,
                'snippet': snippet,
                'has_attachment': bool(latest and latest.attachment),
                'url': url,
                'site_url': site,
            }
            text_body = render_to_string('emails/admin_feedback_new.txt', email_ctx)
            html_body = render_to_string('emails/admin_feedback_new.html', email_ctx)
            send_admin_email(
                subject=f'[Feedback] {title}',
                text_body=text_body,
                html_body=html_body,
                to_emails=emails,
            )

        logger.info(
            'feedback fan-out thread=%s supers=%s in_app=%s email=%s follow_up=%s',
            thread.pk, len(supers), count, len(emails), follow_up,
        )
        return count
    except Exception:
        logger.exception('notify_superadmins_of_feedback failed thread=%s', thread.pk)
        return 0


def notify_user_of_reply(thread, reply_body):
    """A superadmin replied — the user must see it in their regular inbox
    AND on the thread itself. One in-app row; this is a chat, not an alert,
    so no email."""
    try:
        from gallery.notify import notify
        return notify(
            thread.user, 'feedback',
            'The team replied to your feedback',
            (reply_body or '').strip()[:180],
            f'/feedback/{thread.pk}/',
        )
    except Exception:
        logger.exception('notify_user_of_reply failed thread=%s', thread.pk)
        return None


def _last_message_previews(threads):
    """{thread_id: latest FeedbackMessage} without an N+1 per thread."""
    if not threads:
        return {}
    out = {}
    qs = (
        FeedbackMessage.objects
        .filter(thread__in=[t.pk for t in threads])
        .select_related('sender')
        .order_by('thread_id', '-created_at', '-id')
    )
    for m in qs:  # first row per thread is the newest (thread asc, time desc)
        out.setdefault(m.thread_id, m)
    return out


# ---------------------------------------------------------------------
# User side: /feedback/ and /feedback/<pk>/
# ---------------------------------------------------------------------

@ratelimit(key='user', rate='10/h', method='POST')
@login_required
def feedback_inbox(request):
    """GET: your conversations, newest first. POST: start a new one.

    No explicit 429 branch: django-ratelimit's block=True renders one,
    same as follow/tip (users/views.py).
    """
    if request.method == 'POST':
        body, attachment, error = _message_input(
            request,
            'Tell us something first — even one sentence or a screenshot counts.',
        )
        if error:
            messages.error(request, error)
            return redirect('feedback_inbox')
        thread = None
        with transaction.atomic():
            thread = FeedbackThread.objects.create(user=request.user)
            FeedbackMessage.objects.create(
                thread=thread, sender=request.user, from_staff=False,
                body=body, attachment=attachment,
            )
            thread.last_user_message_at = timezone.now()
            thread.save(update_fields=['last_user_message_at'])
        notify_superadmins_of_feedback(thread)
        messages.success(
            request,
            'Sent. A superadmin reads every one — the reply lands back in this '
            'conversation, and a note lands in your inbox.',
        )
        return redirect('feedback_conversation', pk=thread.pk)

    threads = FeedbackThread.objects.filter(user=request.user)
    previews = _last_message_previews(list(threads))
    rows = [(t, previews.get(t.pk)) for t in threads]
    return render(request, 'users/feedback_inbox.html', {'rows': rows})


@ratelimit(key='user', rate='30/h', method='POST')
@login_required
def feedback_conversation(request, pk):
    """The conversation page. Only the owner can read or write it —
    get_object_or_404 is filtered by user, so someone else's pk is a 404,
    not a leak."""
    thread = get_object_or_404(
        FeedbackThread.objects.select_related('user', 'user__profile'),
        pk=pk, user=request.user,
    )
    if request.method == 'POST':
        if thread.status == 'closed':
            messages.error(
                request,
                'This conversation is closed — start a new one from your feedback page.',
            )
            return redirect('feedback_conversation', pk=thread.pk)
        body, attachment, error = _message_input(
            request,
            'Write something first, or attach a screenshot.',
        )
        if error:
            messages.error(request, error)
            return redirect('feedback_conversation', pk=thread.pk)
        had_staff_reply = thread.messages.filter(from_staff=True).exists()
        with transaction.atomic():
            FeedbackMessage.objects.create(
                thread=thread, sender=request.user, from_staff=False,
                body=body, attachment=attachment,
            )
            thread.status = 'open'
            thread.last_user_message_at = timezone.now()
            thread.save(update_fields=['status', 'last_user_message_at'])
        notify_superadmins_of_feedback(thread, follow_up=had_staff_reply)
        messages.success(request, 'Sent — the team will answer in this conversation.')
        return redirect('feedback_conversation', pk=thread.pk)

    return render(request, 'users/feedback_conversation.html', {
        'thread': thread,
        'thread_messages': thread.messages.select_related('sender', 'sender__profile'),
        'is_admin_view': False,
    })


@never_cache
@login_required
def feedback_attachment(request, thread_pk, message_pk):
    """Serve a feedback screenshot only to the conversation participants.

    Feedback images may contain private error screens, account names, or
    tokens. They deliberately do not use ``attachment.url`` in the template,
    and the local public-media server blocks the feedback prefix as a second
    line of defence.
    """
    message = get_object_or_404(
        FeedbackMessage.objects.select_related('thread', 'thread__user'),
        pk=message_pk,
        thread_id=thread_pk,
    )
    is_owner = message.thread.user_id == request.user.pk
    is_superadmin = getattr(getattr(request.user, 'profile', None), 'role', None) == 'superadmin'
    if not is_owner and not is_superadmin:
        raise Http404
    if not message.attachment:
        raise Http404
    try:
        image_file = message.attachment.open('rb')
    except (OSError, ValueError):
        raise Http404
    content_type = mimetypes.guess_type(message.attachment.name)[0] or 'application/octet-stream'
    extension = mimetypes.guess_extension(content_type) or ''
    response = FileResponse(image_file, content_type=content_type)
    response['Content-Disposition'] = f'inline; filename="feedback-screenshot{extension}"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------------------------------------------------------------------
# Superadmin side: the working inbox. /admin/feedback/ and
# /admin/feedback/<pk>/.
# ---------------------------------------------------------------------

def _staff_unread_flag():
    """A thread is unread for staff while ANY user message postdates the
    last read (or it was never read). A null admin_last_read_at must not
    make threads with zero user messages unread — the ordering guards it."""
    return Q(admin_last_read_at__isnull=True) | Q(last_user_message_at__gt=F('admin_last_read_at'))


@superadmin_required
def admin_feedback_queue(request):
    """The superadmin's feedback inbox: unread first, then most recent."""
    threads = (
        FeedbackThread.objects
        .select_related('user', 'user__profile')
        .annotate(staff_unread=Case(
            When(_staff_unread_flag(), last_user_message_at__isnull=False, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ))
        .order_by('-staff_unread', '-last_user_message_at', '-created_at')
    )
    previews = _last_message_previews(list(threads))
    rows = [(t, previews.get(t.pk)) for t in threads]
    return render(request, 'users/feedback_queue.html', {
        'rows': rows,
        'unread_count': threads.filter(staff_unread=1).count(),
    })


@ratelimit(key='user', rate='60/h', method='POST')
@superadmin_required
def admin_feedback_conversation(request, pk):
    """Open one conversation, reply, or close/reopen it.

    Opening it counts as a read (admin_last_read_at) — the badge in the
    nav and the queue ordering both key off that marker.
    """
    thread = get_object_or_404(
        FeedbackThread.objects.select_related('user', 'user__profile'),
        pk=pk,
    )
    if request.method == 'POST':
        action = request.POST.get('action', 'reply')
        now = timezone.now()
        if action in ('close', 'reopen'):
            thread.status = 'closed' if action == 'close' else 'open'
            thread.admin_last_read_at = now
            thread.save(update_fields=['status', 'admin_last_read_at'])
            messages.success(
                request,
                'Conversation closed — the builder can start a new one.'
                if action == 'close'
                else 'Conversation reopened.',
            )
            return redirect('admin_feedback_conversation', pk=thread.pk)
        body, attachment, error = _message_input(
            request,
            'Write a reply first, or attach a screenshot.',
        )
        if error:
            messages.error(request, error)
            return redirect('admin_feedback_conversation', pk=thread.pk)
        with transaction.atomic():
            FeedbackMessage.objects.create(
                thread=thread, sender=request.user, from_staff=True,
                body=body, attachment=attachment,
            )
            thread.status = 'answered'
            thread.admin_last_read_at = now
            thread.save(update_fields=['status', 'admin_last_read_at'])
        notify_user_of_reply(thread, body)
        messages.success(request, 'Reply sent — it is in their inbox and on this thread.')
        return redirect('admin_feedback_conversation', pk=thread.pk)

    if thread.unread_for_staff():
        thread.admin_last_read_at = timezone.now()
        thread.save(update_fields=['admin_last_read_at'])
    return render(request, 'users/feedback_conversation.html', {
        'thread': thread,
        'thread_messages': thread.messages.select_related('sender', 'sender__profile'),
        'is_admin_view': True,
    })
