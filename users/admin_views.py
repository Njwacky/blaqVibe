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
        except Exception: pass
        messages.success(request, f"@{user.username}: {old} → {role}")
    except Exception as e:
        messages.error(request, f"Failed: {e}")
    return redirect('manage_roles')

@admin_required
def audit_log(request):
    logs = AdminLog.objects.select_related('actor').order_by('-created_at')[:50]
    trades = Trade.objects.select_related('buyer','seller','project').order_by('-created_at')[:20]
    return render(request, 'users/audit_log.html', {'logs': logs, 'trades': trades})


# --- Payouts — the money queue (users/payouts.py holds the rules) ----------
# 5 Whys: Why is this admin-facing, not moderator-facing? Only admins+
# may move real money; moderators handle content. Why are full account
# numbers shown here? An admin typing an EFT needs them — this page is
# role-gated and the public side only ever sees the masked digits.

@admin_required
def payout_queue(request):
    from .models import Payout
    from .payouts import payout_rate_label
    from gallery.payments import paystack_enabled
    return render(request, 'users/payout_queue.html', {
        'open_payouts': Payout.objects.select_related('user', 'user__profile').filter(status='requested'),
        'recent_payouts': Payout.objects.select_related('user', 'reviewed_by').exclude(status='requested')[:30],
        'payout_rate_label': payout_rate_label(),
        'paystack_enabled': paystack_enabled(),
        'queue_total_zar': sum(
            p.amount_zar for p in Payout.objects.filter(status='requested')
        ),
    })

@admin_required
@require_POST
def payout_decide(request, payout_id):
    from .models import Payout
    from .payouts import PayoutError, decide_payout
    action = request.POST.get('action', '')
    note = request.POST.get('note', '')

    # "Pay with Paystack" starts a REAL transfer and records its code, but
    # the row only flips to paid when the admin confirms — transfers can
    # fail or wait on OTP for days (never-pretend money rule).
    if action == 'transfer':
        from gallery.payments import initiate_payout_transfer, paystack_enabled
        payout = get_object_or_404(Payout, pk=payout_id, status='requested')
        if not paystack_enabled():
            messages.error(request, 'Paystack is not configured. Record the EFT reference with "Mark paid" instead.')
            return redirect('payout_queue')
        try:
            code = initiate_payout_transfer(payout)
            messages.success(
                request,
                f'Transfer started (code {code}). Confirm it in the Paystack dashboard, '
                f'then "Mark paid" with the reference.'
            )
        except Exception as e:
            messages.error(request, getattr(e, 'message', str(e)))
        return redirect('payout_queue')

    try:
        payout = decide_payout(request.user, payout_id, action, note)
        if action == 'pay':
            messages.success(request, f'Payout #{payout.pk} marked paid — R{payout.amount_zar} to @{payout.user.username if payout.user else "?"}.')
        else:
            messages.success(request, f'Payout #{payout.pk} rejected — {payout.amount_stars} ★ returned to the wallet.')
    except PayoutError as e:
        messages.error(request, e.message)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('payout decide view crush')
        messages.error(request, 'Decision failed — nothing changed. Try again.')
    return redirect('payout_queue')
