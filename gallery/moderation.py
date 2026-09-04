from django.contrib import messages
from django.db.models import Count
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from users.decorators import moderator_required
from .models import AppProject, AppReport
from .reports import resolve_report

@moderator_required
def moderation_queue(request):
    pending = AppProject.objects.filter(status='pending').select_related('owner','category').order_by('-created_at')
    quarantined = AppProject.objects.filter(status='quarantined').select_related('owner','category').order_by('-created_at')
    open_reports = AppReport.objects.filter(status='open').count()
    return render(request, 'gallery/moderation_queue.html', {
        'pending': pending,
        'quarantined': quarantined,
        'open_reports': open_reports,
    })


@moderator_required
def reports_queue(request):
    """Report triage — open reports first, resolved/ignored behind them.

    5 Whys:
    1. Why split open vs handled? A moderator's job today is the open pile;
       putting handled rows first hides it. The queue is a work order, not
       an archive.
    2. Why group by count on the project row? Seeing "3 open reports on
       this vibe" is how you know a report is credible before you open it;
       one report can be noise, three is a pattern.
    3. Why include a project lock banner on the action buttons? The best
       moderator decision (quarantine/remove/delete) changes content; the
       row must say plainly that Approve alone never does that.
    4. Why include a note field? A resolution without a reason is an action
       a lawyer or a support ticket cannot reconstruct.
    5. Why show resolved/ignored at all on the same page? A moderator who
       acted "too fast" needs to find the row again; a separate history URL
       would be one more page for a rare need.
    """
    now = timezone.now()
    open_reports = (
        AppReport.objects
        .filter(status='open')
        .select_related('project', 'project__owner', 'user', 'user__profile')
        .order_by('-created_at')
    )
    open_counts = dict(
        AppReport.objects
        .filter(status='open')
        .values('project_id')
        .annotate(n=Count('id'))
        .values_list('project_id', 'n')
    )
    open_list = list(open_reports)
    for report in open_list:
        report.project_open_count = open_counts.get(report.project_id, 1)
        report.project_status = report.project.status

    handled = (
        AppReport.objects
        .exclude(status='open')
        .select_related('project', 'project__owner', 'user', 'handled_by')
        .order_by('-handled_at')[:50]
    )

    stats = {
        'open': len(open_list),
        'resolved': AppReport.objects.filter(status='resolved').count(),
        'ignored': AppReport.objects.filter(status='ignored').count(),
        'projects_flagged': len({r.project_id for r in open_list}),
    }
    return render(request, 'gallery/reports_queue.html', {
        'reports': open_list,
        'handled': handled,
        'stats': stats,
        'now': now,
        'decisions': (
            ('ignore', 'Dismiss — no action'),
            ('quarantine', 'Hold / quarantine vibe'),
            ('remove', 'Remove vibe (admin)'),
            ('delete', 'Delete vibe (admin)'),
        ),
    })


@moderator_required
@require_POST
def report_action(request, report_id):
    """Handle one report. POST only; every action is a state change."""
    report = get_object_or_404(
        AppReport.objects.select_related('project', 'project__owner'),
        pk=report_id,
    )
    decision = request.POST.get('decision', '')
    note = request.POST.get('note', '')
    try:
        from .prompt_sanitize import sanitize_prompt
        note = sanitize_prompt(note or '')[:500]
    except Exception:
        note = (note or '')[:500]

    result = resolve_report(report, request.user, decision, note)
    if result.get('ok'):
        messages.success(request, result['message'])
    else:
        messages.error(request, result.get('message', 'Could not resolve this report.'))
    next_url = request.POST.get('next', '')
    if next_url.startswith('/reports/'):
        return redirect(next_url)
    return redirect('reports_queue')

@moderator_required
@require_POST
def moderation_action(request, slug):
    project = get_object_or_404(AppProject, slug=slug)
    action = request.POST.get('action')
    if action == 'approve':
        project.status = 'published'
        project.save(update_fields=['status'])
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
        from .lifecycle import remove_project
        remove_project(project)
        return redirect('moderation_queue')
    return redirect('moderation_queue')
