from datetime import timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from django.db.models import Count, Sum
from django_ratelimit.decorators import ratelimit
from django.db.models.functions import TruncDate

from .decorators import admin_required, superadmin_required
from .models import AdminLog, FooterContact, Profile
from .forms import FooterContactFormSet
from .footer_contacts import (
    MAX_FOOTER_CONTACTS,
    kind_guide,
    next_positions,
    public_footer_contacts,
)
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

    # Quarantine rate over scans WITH a conclusive outcome.
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

    # Open first, then handled; the dashboard should show the work, not the
    # archive. We keep it small (10) because the triage page owns the detail.
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
@ratelimit(key='user', rate='10/h', method='POST')
@require_http_methods(['GET', 'POST'])
def footer_contacts(request):
    """Maintain the public footer contact list without a deploy.

    The footer is a LIST of methods, not three fixed fields: a company with two
    support mailboxes, a WhatsApp number and an X account adds a row for each
    and saves. Rows are ordered by `position`, can be hidden without being
    deleted, and the public footer renders whatever is active.
    """
    queryset = FooterContact.objects.all()

    if request.method == 'POST':
        if getattr(request, 'limited', False):
            messages.error(request, 'Rate limit: try changing footer contacts again in an hour.')
            return redirect('footer_contacts')

        formset = FooterContactFormSet(request.POST, queryset=queryset)
        if formset.is_valid():
            kept = [
                form for form in formset.forms
                if form.cleaned_data.get('value') and not form.cleaned_data.get('DELETE')
            ]
            if len(kept) > MAX_FOOTER_CONTACTS:
                messages.error(
                    request,
                    f'Keep the footer to {MAX_FOOTER_CONTACTS} contact methods so it stays '
                    f'readable — remove one to add another.',
                )
                return _render_footer_contacts(request, formset)

            added, updated, removed = _save_footer_contacts(formset)
            if added or updated or removed:
                # Audit the kinds, not the values: the log must stay useful
                # without copying public contact details into another table.
                AdminLog.objects.create(
                    actor=request.user,
                    action='update_footer_contacts',
                    target=_footer_audit_target(added, updated, removed),
                )
                messages.success(request, 'Footer contacts updated.')
            else:
                messages.info(request, 'No footer contact changes to save.')
            return redirect('footer_contacts')
    else:
        formset = FooterContactFormSet(
            queryset=queryset,
            initial=[{'position': position} for position in next_positions(2)],
        )

    return _render_footer_contacts(request, formset)


def _save_footer_contacts(formset):
    """Apply the formset. Returns (added, updated, removed) for the audit log.

    commit=False is used so deletions are handled here, in one place, instead
    of being a side effect of save() that the audit summary cannot see.
    """
    # save() is what populates deleted_objects (and it deletes nothing while
    # commit is False), so it has to run before the removals are counted.
    instances = formset.save(commit=False)
    removed = list(formset.deleted_objects)
    added = [instance for instance in instances if instance.pk is None]
    updated = [instance for instance in instances if instance.pk is not None]
    for instance in instances:
        instance.save()
    for instance in removed:
        instance.delete()
    return added, updated, removed


def _footer_audit_target(added, updated, removed):
    parts = []
    for verb, rows in (('added', added), ('updated', updated), ('removed', removed)):
        if rows:
            kinds = ', '.join(sorted({row.kind for row in rows}))
            parts.append(f'{verb}: {kinds}')
    return '; '.join(parts) or 'no change'


def _render_footer_contacts(request, formset):
    return render(request, 'users/footer_contacts.html', {
        'formset': formset,
        'footer_preview': public_footer_contacts(),
        'kind_guide': kind_guide(),
        'max_contacts': MAX_FOOTER_CONTACTS,
    })


@admin_required
def audit_log(request):
    logs = AdminLog.objects.select_related('actor').order_by('-created_at')[:50]
    trades = Trade.objects.select_related('buyer','seller','project').order_by('-created_at')[:20]
    return render(request, 'users/audit_log.html', {'logs': logs, 'trades': trades})

