from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django_ratelimit.decorators import ratelimit

from .models import AppProject
from .taxonomy import PROGRAM_KINDS

def _serialize(project):
    """Public JSON for one vibe.
    """
    return {
        'slug': project.slug,
        'title': project.title,
        'description': project.short_description,
        'owner': project.owner.username,
        'tech_stack': project.tech_stack,
        'stars': project.stars,
        'clones': project.clones,
        'star_cost': int(project.star_cost or 0),
        'price_zar': int(project.price_zar or 0),
        'url': project.get_absolute_url(),
        # Legacy key: packaging, not program type. Kept for existing clients.
        'kind': 'full_app' if project.zip_file else 'snippet',
        'program_kind': project.kind,
        'program_kind_label': project.kind_label,
        'preview': project.preview_mode,
        'can_run_preview': project.can_run_preview,
        'appeal_score': round(float(project.appeal_score or 0), 2),
        # Trust tier — the public verdict string, never the backend
        # scan_report (which holds secret filenames and audit detail).
        # 4 points: (1) add-only change keeps the API backwards compatible;
        # (2) the tier is the same value the website badge renders, so API
        # consumers cannot see a different trust story than the site;
        # (3) 'trust_label' gives non-dev consumers a printable word;
        # (4) pipeline-written only, so the API can never be asked to
        # change it — read-only by construction.
        'trust': project.trust,
        'trust_label': project.trust_meta['label'],
    }

@ratelimit(key='ip', rate='120/m')
def api_apps(request):
    """Published vibes. `?program=game` filters, `?sort=interesting` ranks."""
    qs = AppProject.objects.filter(status='published').select_related('owner')
    program = (request.GET.get('program') or '').strip().lower()
    valid = {k['value'] for k in PROGRAM_KINDS}
    if program in valid:
        qs = qs.filter(kind=program)
    # Trust filter — same whitelist rule as the website feed ('verified' |
    # 'scanned'; anything else is ignored). 4 points: (1) API consumers get
    # the same instruction the site honours — no divergent trust story;
    # (2) add-only parameter, existing consumers are unaffected; (3) rides
    # the trust db_index so the endpoint stays a range scan; (4) an empty
    # result set is an honest answer, never silently refilled.
    trust = (request.GET.get('trust') or '').strip().lower()
    if trust in ('verified', 'scanned'):
        qs = qs.filter(trust=trust)
    if (request.GET.get('sort') or '') == 'interesting':
        qs = qs.order_by('-appeal_score', '-created_at')
    else:
        qs = qs.order_by('-created_at')
    return JsonResponse({'results': [_serialize(p) for p in qs[:50]]})

@ratelimit(key='ip', rate='120/m')
def api_app_detail(request, slug):
    project = get_object_or_404(
        AppProject.objects.select_related('owner'),
        slug=slug,
        status='published',
    )
    data = _serialize(project)
    data['readme'] = project.readme
    data['file_count'] = project.file_count
    return JsonResponse(data)

@ratelimit(key='ip', rate='120/m')
def api_program_kinds(request):
    """The taxonomy itself — so a client never hard-codes our labels."""
    return JsonResponse({'results': [
        {'value': k['value'], 'label': k['label'], 'icon': k['icon'],
         'blurb': k['blurb'], 'preview': k['preview']}
        for k in PROGRAM_KINDS
    ]})
