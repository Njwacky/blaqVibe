from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from .models import AppProject


def _serialize(project):
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
        'kind': 'full_app' if project.zip_file else 'snippet',
    }


def api_apps(request):
    qs = (
        AppProject.objects.filter(status='published')
        .select_related('owner')
        .order_by('-created_at')[:50]
    )
    return JsonResponse({'results': [_serialize(p) for p in qs]})


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
