from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from users.decorators import moderator_required
from .models import AppProject

@moderator_required
def moderation_queue(request):
    pending = AppProject.objects.filter(status='pending').select_related('owner','category').order_by('-created_at')
    quarantined = AppProject.objects.filter(status='quarantined').select_related('owner','category').order_by('-created_at')
    return render(request, 'gallery/moderation_queue.html', {'pending': pending, 'quarantined': quarantined})

@moderator_required
@require_POST
def moderation_action(request, slug):
    project = get_object_or_404(AppProject, slug=slug)
    action = request.POST.get('action')
    if action == 'approve':
        project.status = 'published'
        project.save(update_fields=['status'])
        # Human approval is a publish path too — grade from the recorded
        # evidence (snippet_scan for snippets, the scan chain for ZIPs) so
        # a moderator-approved vibe carries the same badge machinery.
        try:
            from .trust import apply_trust_grade
            apply_trust_grade(project)
        except Exception:
            pass
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
