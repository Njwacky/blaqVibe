from datetime import timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate

from .decorators import admin_required, superadmin_required
from .models import Profile, AdminLog
from .charts import daily_bars_chart, h_bars_chart
from gallery.models import AppProject, AppReport, CloneEvent, ScanJob, Trade

DAYS = 14



def _fmt(v):
    if v is None:
        return '0'
    if v >= 1000:
        return f'{v / 1000:.1f}k'
    return str(int(v))


def _days_list():
    today = timezone.localdate()
    return [today - timedelta(days=i) for i in reversed(range(DAYS))]


def _daily_counts(queryset, field, days, aggregate='count'):
    """Map TruncDate(field) -> n for the given queryset, filled to `days`."""
    agg = Count('id') if aggregate == 'count' else Sum('cost')
    rows = queryset.annotate(day=TruncDate(field)).values('day').annotate(n=agg)
    counts = {r['day']: (r['n'] or 0) for r in rows}
    return [counts.get(d, 0) for d in days]


@admin_required
def admin_dashboard(request):
    days = _days_list()
    start = days[0]
    since = timezone.now() - timedelta(days=DAYS - 1)

    stats = {
        'total_vibes': AppProject.objects.count(),
        'published': AppProject.objects.filter(status='published').count(),
        'pending': AppProject.objects.filter(status='pending').count(),
        'quarantined': AppProject.objects.filter(status='quarantined').count(),
        'total_trades': Trade.objects.count(),
        'reports': AppReport.objects.count(),
        'open_reports': AppReport.objects.filter(status='open').count(),
        'users': User.objects.count(),
        'total_clones': AppProject.objects.aggregate(n=Sum('clones'))['n'] or 0,
    }

    scan_q = ScanJob.objects.filter(status='quarantined').count()
    scan_clean = ScanJob.objects.filter(status='clean').count()
    stats['quarantine_rate'] = round(scan_q / (scan_q + scan_clean) * 100, 1) if (scan_q + scan_clean) else None

    trade_rows = Trade.objects.filter(created_at__date__gte=start)
    charts = {
        'clones': daily_bars_chart(
            'Clones per day, last 14 days',
            days,
            [{'name': 'clones', 'color': '#8B5CF6',
              'values': _daily_counts(CloneEvent.objects.filter(created_at__date__gte=start), 'created_at', days)}],
            fmt=_fmt,
        ),
        'trades': daily_bars_chart(
            'Trades per day, last 14 days',
            days,
            [{'name': 'trades', 'color': '#10B981',
              'values': _daily_counts(trade_rows, 'created_at', days)}],
            fmt=_fmt,
        ),
        'star_volume': daily_bars_chart(
            'Stars moved per day, last 14 days',
            days,
            [{'name': '★ volume', 'color': '#F59E0B',
              'values': _daily_counts(trade_rows, 'created_at', days, aggregate='sum')}],
            fmt=_fmt,
        ),
        'signups': daily_bars_chart(
            'Signups per day, last 14 days',
            days,
            [{'name': 'signups', 'color': '#3B82F6',
              'values': _daily_counts(User.objects.filter(date_joined__date__gte=start), 'date_joined', days)}],
            fmt=_fmt,
        ),
        'uploads': daily_bars_chart(
            'Vibes uploaded per day, last 14 days',
            days,
            [{'name': 'uploads', 'color': '#EC4899',
              'values': _daily_counts(AppProject.objects.filter(created_at__date__gte=start), 'created_at', days)}],
            fmt=_fmt,
        ),
        'scans': daily_bars_chart(
            'Scan outcomes per day, last 14 days (stacked)',
            days,
            [
                {'name': 'clean', 'color': '#10B981',
                 'values': _daily_counts(ScanJob.objects.filter(status='clean', updated_at__date__gte=start), 'updated_at', days)},
                {'name': 'quarantined', 'color': '#EF4444',
                 'values': _daily_counts(ScanJob.objects.filter(status='quarantined', updated_at__date__gte=start), 'updated_at', days)},
                {'name': 'failed', 'color': '#F59E0B',
                 'values': _daily_counts(ScanJob.objects.filter(status='failed', updated_at__date__gte=start), 'updated_at', days)},
            ],
            stacked=True,
            fmt=_fmt,
        ),
    }
    stats['star_volume_14d'] = sum(
        (r['n'] or 0) for r in trade_rows.annotate(day=TruncDate('created_at')).values('day').annotate(n=Sum('cost'))
    )

    top_stars = list(
        AppProject.objects.filter(status='published').order_by('-stars')[:8]
    )
    top_clones_rows = list(
        CloneEvent.objects.filter(created_at__gte=since)
        .values('project_id').annotate(n=Count('id')).order_by('-n')[:8]
    )
    top_clone_projects = {
        p.pk: p for p in AppProject.objects.filter(
            pk__in=[r['project_id'] for r in top_clones_rows]
        )
    }
    charts['top_stars'] = h_bars_chart(
        'Top vibes by stars',
        [{'label': p.title, 'value': p.stars} for p in top_stars if p.stars],
        hrefs=[p.get_absolute_url() for p in top_stars if p.stars],
        fmt=_fmt,
        bar_color='#F59E0B',
    )
    charts['top_clones'] = h_bars_chart(
        'Top vibes by clones, last 30 days',
        [{'label': top_clone_projects[r['project_id']].title, 'value': r['n']}
         for r in top_clones_rows if r['project_id'] in top_clone_projects],
        hrefs=[top_clone_projects[r['project_id']].get_absolute_url()
               for r in top_clones_rows if r['project_id'] in top_clone_projects],
        fmt=_fmt,
        bar_color='#8B5CF6',
    )

    recent_reports = (
        AppReport.objects
        .select_related('project', 'project__owner', 'user', 'handled_by')
        .order_by('status', '-created_at')[:10]
    )
    return render(request, 'users/admin_dashboard.html', {
        'stats': stats,
        'charts': charts,
        'recent_reports': recent_reports,
    })

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
    if user == request.user and role != 'superadmin':
        messages.error(request, "You cannot demote yourself")
        return redirect('manage_roles')
    profile,_ = Profile.objects.get_or_create(user=user)
    old = profile.role
    profile.role = role
    try:
        profile.save(update_fields=['role'])
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
