from datetime import timedelta
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.vary import vary_on_headers
from django.db.models import Case, Count, IntegerField, Sum, Value, When
from django_ratelimit.decorators import ratelimit
from django.db.models.functions import TruncDate

from . import account_cleanup
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


AUDIT_PAGE_SIZE = 50

@admin_required
def audit_log(request):
    # Paged, not sliced: a bulk clean-up writes one row per account, and a
    # hard "latest 50" would let a single batch push every older decision out
    # of reach of this page.
    logs = Paginator(
        AdminLog.objects.select_related('actor').order_by('-created_at', '-pk'), AUDIT_PAGE_SIZE,
    ).get_page(request.GET.get('page'))
    trades = Trade.objects.select_related('buyer','seller','project').order_by('-created_at')[:20]
    return render(request, 'users/audit_log.html', {'logs': logs, 'trades': trades})


# ----------------------------------------------------------------------
# Quarantine clean-up: find held accounts, review the blast radius, delete in
# bulk. Every decision lives in users/account_cleanup.py; these views carry the
# request in and the result out, so they stay thin enough to read in one go.
# ----------------------------------------------------------------------
def _pager(data):
    """Numbered links with gaps (1 … 4 5 [6] 7 8 … 40), URLs built from the
    filters so paging never drops the search."""
    page = data.page
    last = page.paginator.num_pages
    base = reverse('quarantined_accounts')

    def url(n):
        query = urlencode(data.filters.params(page=n))
        return f'{base}?{query}' if query else base

    shown = sorted({1, last, *range(max(1, page.number - 2), min(last, page.number + 2) + 1)})
    items, previous = [], None
    for n in shown:
        if previous is not None and n - previous > 1:
            items.append({'gap': True})
        items.append({'n': n, 'url': url(n), 'current': n == page.number})
        previous = n
    return {
        'items': items,
        'prev': url(page.number - 1) if page.has_previous() else '',
        'next': url(page.number + 1) if page.has_next() else '',
        'multiple': last > 1,
    }


@admin_required
# One URL, two shapes, so the browser must never reuse one for the other. Found
# the hard way: without these, typing in the search box, leaving, and pressing
# Back rendered the bare results fragment — no nav, no styles, no filters —
# because the HTTP cache had stored the script's partial under the page's URL.
# `no-store` is also right on its own terms: this is per-account admin data that
# is stale the moment anyone lifts a hold.
@never_cache
@vary_on_headers('X-Requested-With')
@require_http_methods(['GET', 'HEAD'])
def quarantined_accounts(request):
    """Search, filter and page the accounts that are held right now.

    One URL, two shapes: the full page, and (for the page's own script, which
    sends X-Requested-With) just the results block — so live search replaces
    a table instead of reloading the site around it.
    """
    filters = account_cleanup.parse_filters(request.GET)
    data = account_cleanup.listing(
        request.user, filters, include_email=account_cleanup.can_search_email(request.user),
    )
    context = {
        'data': data,
        'filters': filters,
        'pager': _pager(data),
        'shows': account_cleanup.SHOWS,
        'sorts': account_cleanup.SORTS,
        'page_sizes': account_cleanup.PAGE_SIZES,
        'searches_email': account_cleanup.can_search_email(request.user),
        'reset_selection': request.GET.get('cleared') == '1',
    }
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        response = render(request, 'users/_quarantined_results.html', context)
        # The script checks this before it swaps anything in, so an expired
        # session (a redirect to the login page) is never pasted into the table.
        response['X-QA-Partial'] = '1'
        return response
    return render(request, 'users/quarantined_accounts.html', context)


def _review_context(review, *, reason='', notify=None):
    return {
        'review': review,
        'submitted_reason': reason,
        'submitted_notify': (not review.only_spam) if notify is None else notify,
        'reason_min': account_cleanup.REASON_MIN,
        'reason_max': account_cleanup.REASON_MAX,
        'min_hold_hours': account_cleanup.min_hold_hours(),
    }


@admin_required
# The review names people and their holds; like the list, it must not sit in a
# browser or proxy cache after the admin has left.
@never_cache
# block=False: django-ratelimit 4.x blocks by default, which raises before the
# friendly `request.limited` branch below could answer (see gallery/skill_views).
# The rate is a callable so the setting is read per request (ACCOUNT_CLEANUP_REVIEW_RATE).
@ratelimit(key='user', rate=account_cleanup.review_rate_for, method='POST', block=False)
@require_POST
def quarantined_review(request):
    """The dry run: show exactly who goes, who is refused, and what it costs.

    Changes nothing. POST (not GET) because the ids travel in the body — a
    hundred of them would not survive in a URL — and because nothing that
    names accounts to act on should be a link someone can paste into chat.
    """
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many requests — try again in a minute.')
        return redirect('quarantined_accounts')
    review = account_cleanup.review(request.user, request.POST.getlist('ids'))
    if not review.requested:
        messages.error(request, 'Select at least one account first.')
        return redirect('quarantined_accounts')
    if review.over_limit:
        messages.error(
            request,
            f'You selected more than {review.limit} accounts. Deselect some — {review.limit} per batch '
            'keeps every delete fast and every review readable.',
        )
        return redirect('quarantined_accounts')
    return render(request, 'users/quarantined_review.html', _review_context(review))


def _skip_line(prefix, items, limit=4):
    shown = '; '.join(f'{item.label} — {item.reasons[0]}' for item in items[:limit])
    more = f' … and {len(items) - limit} more' if len(items) > limit else ''
    return f'{prefix} {len(items)}: {shown}{more}'


def _count(n, word):
    return f'{n} {word}{"" if n == 1 else "s"}'


def _flash_result(request, result):
    deleted = len(result.deleted)
    if deleted:
        kept = f'; {_count(result.vibes_kept, "sold vibe")} kept for their buyers' if result.vibes_kept else ''
        messages.success(
            request,
            f'Deleted {_count(deleted, "account")} '
            f'({_count(result.vibes_deleted, "vibe")} deleted{kept}). Logged as batch {result.batch}.',
        )
    else:
        messages.warning(request, 'Nothing was deleted.')
    if result.skipped:
        messages.warning(request, _skip_line('Skipped', result.skipped))
    if result.failed:
        messages.error(request, _skip_line('Failed (nothing changed for these)', result.failed))
    if result.notified or result.not_notified:
        note = f'Notice emailed to {result.notified} of {result.notified + result.not_notified}.'
        if result.not_notified:
            note += ' The rest were not sent (mail provider slow or refused).'
        messages.info(request, note)


@admin_required
@never_cache
@ratelimit(key='user', rate=account_cleanup.delete_rate_for, method='POST', block=False)  # ACCOUNT_CLEANUP_DELETE_RATE
@require_POST
def quarantined_delete(request):
    """The one destructive step. Nothing here trusts the page that sent it:
    the engine re-checks the confirmation, the reason and every account."""
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many deletions in the last hour — try again later.')
        return redirect('quarantined_accounts')
    ids = request.POST.getlist('ids')
    reason = request.POST.get('reason', '')
    notify = request.POST.get('notify') == 'on'
    result = account_cleanup.delete_quarantined_accounts(
        actor=request.user, ids=ids, reason=reason,
        confirm=request.POST.get('confirm', ''), notify=notify,
    )
    if not result.ok:
        messages.error(request, result.error)
        review = account_cleanup.review(request.user, ids)
        if review.over_limit or not (review.rows or review.skipped):
            return redirect('quarantined_accounts')
        # Refused: show the same review again with what the operator typed.
        return render(request, 'users/quarantined_review.html',
                      _review_context(review, reason=reason, notify=notify))
    _flash_result(request, result)
    # `cleared` tells the page's script to forget its remembered selection.
    return redirect(f"{reverse('quarantined_accounts')}?cleared=1")

