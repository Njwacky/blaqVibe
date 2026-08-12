from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.views.decorators.http import require_POST
from .decorators import admin_required, superadmin_required
from .models import Profile, AdminLog
from gallery.models import AppProject, AppReport, Trade

@admin_required
def admin_dashboard(request):
    # Stats — backend only, no JS secrets
    stats = {
        'total_vibes': AppProject.objects.count(),
        'published': AppProject.objects.filter(status='published').count(),
        'pending': AppProject.objects.filter(status='pending').count(),
        'quarantined': AppProject.objects.filter(status='quarantined').count(),
        'total_trades': Trade.objects.count(),
        'reports': AppReport.objects.count(),
        'users': User.objects.count(),
    }
    top_creators = User.objects.all()[:10]  # simple
    recent_reports = AppReport.objects.select_related('project','user').order_by('-created_at')[:10]
    return render(request, 'users/admin_dashboard.html', {'stats': stats, 'top_creators': top_creators, 'recent_reports': recent_reports})

@superadmin_required
def manage_roles(request):
    users = User.objects.select_related('profile').all().order_by('username')
    logs = AdminLog.objects.select_related('actor').order_by('-created_at')[:10]
    return render(request, 'users/manage_roles.html', {'users': users, 'logs': logs})

@superadmin_required
@require_POST
def set_role(request, username):
    user = get_object_or_404(User, username=username)
    role = request.POST.get('role')
    if role not in ('user','moderator','admin','superadmin'):
        messages.error(request, "Invalid role")
        return redirect('manage_roles')
    # Prevent demoting self
    if user == request.user and role != 'superadmin':
        messages.error(request, "You cannot demote yourself")
        return redirect('manage_roles')
    profile,_ = Profile.objects.get_or_create(user=user)
    old = profile.role
    profile.role = role
    try:
        profile.save(update_fields=['role'])
        # Audit log — backend only
        try:
            AdminLog.objects.create(actor=request.user, action='set_role', target=f"@{user.username}: {old}→{role}")
        except: pass
        messages.success(request, f"@{user.username}: {old} → {role}")
    except Exception as e:
        messages.error(request, f"Failed: {e}")
    return redirect('manage_roles')

@admin_required
def audit_log(request):
    logs = AdminLog.objects.select_related('actor').order_by('-created_at')[:50]
    trades = Trade.objects.select_related('buyer','seller','project').order_by('-created_at')[:20]
    return render(request, 'users/audit_log.html', {'logs': logs, 'trades': trades})
