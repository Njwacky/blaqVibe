from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from users.decorators import moderator_required
from .models import AppProject, Comment, CommentReport


def _hide_comment(comment):
    """Hide a comment in-app. Raw body stays for moderators (admin shows
    Comment.body); the public body_html becomes the standard notice.

    Why a queryset update instead of comment.save()? save() only hides
    when the language gate trips, and it re-renders body_html from the
    raw body — a moderator hides for ANY reason (harassment, spam), so
    this writes the hidden state and the notice directly.
    """
    Comment.objects.filter(pk=comment.pk).update(
        is_hidden=True,
        body_html=(
            '<p>This comment was hidden because it used language '
            'that is not allowed here.</p>'
        ),
    )


@moderator_required
def moderation_queue(request):
    pending = AppProject.objects.filter(status='pending').select_related('owner','category').order_by('-created_at')
    quarantined = AppProject.objects.filter(status='quarantined').select_related('owner','category').order_by('-created_at')
    # The in-app comment queue: open reports first (what visitors asked
    # us to look at), plus hidden comments so a moderator can unhide a
    # false positive without leaving the queue.
    comment_reports = CommentReport.objects.filter(resolved=False).select_related(
        'comment', 'comment__user', 'comment__project', 'reporter',
    ).order_by('-created_at')[:100]
    hidden_comments = Comment.objects.filter(is_hidden=True).select_related(
        'user', 'project',
    ).order_by('-created_at')[:50]
    return render(request, 'gallery/moderation_queue.html', {
        'pending': pending,
        'quarantined': quarantined,
        'comment_reports': comment_reports,
        'hidden_comments': hidden_comments,
    })

@moderator_required
@require_POST
def moderation_action(request, slug):
    project = get_object_or_404(AppProject, slug=slug)
    action = request.POST.get('action')
    if action == 'approve':
        project.status = 'published'
        project.save(update_fields=['status'])
        from .models import ScanJob
        ScanJob.objects.update_or_create(project=project, defaults={'status': 'clean'})
    elif action == 'reject':
        project.status = 'quarantined'
        project.save(update_fields=['status'])
        from .models import ScanJob
        ScanJob.objects.update_or_create(project=project, defaults={'status': 'quarantined'})
    elif action == 'delete':
        if not request.user.profile.is_admin():
            return render(request, '403.html', status=403)
        # Same rule as owner deletes: paid vibes soft-delete so buyers keep
        # their receipts and downloads; unpaid vibes hard-delete.
        from .lifecycle import remove_project
        remove_project(project)
        return redirect('moderation_queue')
    return redirect('moderation_queue')


@moderator_required
@require_POST
def comment_report_action(request, report_id):
    """Handle one comment report: hide the comment or dismiss the report."""
    report = get_object_or_404(CommentReport, pk=report_id)
    action = request.POST.get('action')
    comment = report.comment
    if action == 'hide':
        _hide_comment(comment)
        report.resolved = True
        report.save(update_fields=['resolved'])
        messages.success(request, f"Comment #{comment.pk} hidden; report resolved.")
    elif action == 'dismiss':
        report.resolved = True
        report.save(update_fields=['resolved'])
        messages.info(request, f"Report on comment #{comment.pk} dismissed.")
    return redirect('moderation_queue')


@moderator_required
@require_POST
def comment_action(request, comment_id):
    """Direct hide/unhide for one comment — the in-app replacement for
    the admin-only is_hidden toggle. Also resolves any open reports on
    the comment, since the moderator just made the call."""
    comment = get_object_or_404(Comment, pk=comment_id)
    action = request.POST.get('action')
    if action == 'hide':
        _hide_comment(comment)
        CommentReport.objects.filter(comment=comment, resolved=False).update(resolved=True)
        messages.success(request, f"Comment #{comment.pk} hidden.")
    elif action == 'unhide':
        # False positive: render the real body again. Comment.save()
        # re-renders body_html from the raw body — a full save, so the
        # new HTML persists together with the flag.
        comment.is_hidden = False
        comment.save()
        messages.success(request, f"Comment #{comment.pk} unhidden.")
    return redirect('moderation_queue')
