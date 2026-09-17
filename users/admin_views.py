from datetime import timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.db.models import Case, Count, IntegerField, Sum, Value, When
from django_ratelimit.decorators import ratelimit
from django.db.models.functions import TruncDate

from .decorators import admin_required, superadmin_required
from .models import AdminLog, FooterContact, Profile
from .roles import ROLE_GUIDE, ROLE_ORDER, apply_role_change
from .user_search import elevated_rows, role_totals, search_users, users_with_role
from .forms import FooterContactFormSet
from .footer_contacts import (
    MAX_FOOTER_CONTACTS,
    kind_guide,
    next_positions,
    public_footer_contacts,
)
from .charts import daily_bars_chart, h_bars_chart
from gallery.models import AppProject, AppReport, CloneEvent, ScanJob, Trade
from .models import QuarantineAppeal, RuleViolation, UserQuarantine

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
        # Account quarantine (users/quarantine.py) — people decisions, not
        # scan verdicts, so they are counted separately from `quarantined` above.
        'accounts_quarantined': UserQuarantine.objects.filter(status='active', ends_at__gt=timezone.now()).count(),
        'open_appeals': QuarantineAppeal.objects.filter(status='open').count(),
        'violations_7d': RuleViolation.objects.filter(created_at__gte=timezone.now() - timedelta(days=7)).count(),
        'quarantines_lifted': UserQuarantine.objects.filter(status='lifted').count(),
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
    # Appeals first: they are a person waiting on a human answer. Alphabetical
    # status order would put 'accepted' above 'open' — an explicit CASE keeps
    # the unanswered ones on top where they belong.
    recent_appeals = (
        QuarantineAppeal.objects
        .select_related('user', 'quarantine')
        .annotate(
            awaiting=Case(
                When(status='open', then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by('awaiting', '-created_at')[:8]
    )
    return render(request, 'users/admin_dashboard.html', {
        'stats': stats,
        'charts': charts,
        'recent_reports': recent_reports,
        'recent_appeals': recent_appeals,
    })

@superadmin_required
def manage_roles(request):
    """Find a person. The page that used to list every user, now searches.

    Three states, one URL:
      ?q=          ranked, bounded search results
      ?role=       everyone holding one role (access review)
      (neither)    role totals + who holds elevated access + recent changes
    """
    query = (request.GET.get('q') or '').strip()
    role = (request.GET.get('role') or '').strip()
    if role not in ROLE_ORDER:
        role = ''

    results = None
    role_rows = []
    role_total = 0
    if query:
        results = search_users(query)
        # One unambiguous hit: the operator typed the identifier they already
        # knew, so go to the person instead of a one-row list.
        exact = results.single_exact
        if exact is not None and not role:
            return redirect('manage_user_role', username=exact.username)
    elif role:
        role_rows, role_total = users_with_role(role)

    return render(request, 'users/manage_roles.html', {
        'query': results.query if results else query,
        'raw_query': query,
        'results': results,
        'role': role,
        'role_rows': role_rows,
        'role_total': role_total,
        'role_guide': ROLE_GUIDE,
        'role_totals': role_totals(),
        'elevated_rows': elevated_rows(),
        'logs': AdminLog.objects.select_related('actor').order_by('-created_at')[:10],
        'total_users': User.objects.count(),
    })


@superadmin_required
@ratelimit(key='user', rate='20/h', method='POST')
@require_http_methods(['GET', 'POST'])
def manage_user_role(request, username):
    """One person, one page: their account state, their role, the change.

    POST is the same view as GET on purpose — a guard that refuses the change
    re-renders the form with everything the operator typed (the reason they
    just wrote is not thrown away by a bounce to the list).
    """
    user = get_object_or_404(User.objects.select_related('profile'), username=username)

    if request.method == 'POST':
        if getattr(request, 'limited', False):
            messages.error(request, 'Too many role changes — try again in a minute.')
            return redirect('manage_user_role', username=user.username)

        result = apply_role_change(
            actor=request.user,
            target=user,
            new_role=request.POST.get('role', ''),
            reason=request.POST.get('reason', ''),
            confirm=request.POST.get('confirm', ''),
        )
        getattr(messages, result.level, messages.error)(request, result.message)
        if result.changed:
            return redirect('manage_user_role', username=user.username)
        # Refused: fall through and re-render with the submitted values.
        submitted_reason = request.POST.get('reason', '')
    else:
        submitted_reason = ''

    profile, _created = Profile.objects.get_or_create(user=user)
    history = (AdminLog.objects
               .filter(target__startswith=f'@{user.username}:')
               .select_related('actor')
               .order_by('-created_at')[:10])
    return render(request, 'users/manage_role_detail.html', {
        'target': user,
        'target_profile': profile,
        'current_role': profile.role,
        'role_guide': ROLE_GUIDE,
        'role_totals': role_totals(),
        'history': history,
        'submitted_reason': submitted_reason,
        'project_count': user.projects.count(),
        'is_self': user.pk == request.user.pk,
    })

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

