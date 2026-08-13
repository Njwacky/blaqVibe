"""Public, read-only launch guidance views."""

from django.http import Http404
from django.shortcuts import render

from .launch_guides import (
    ARTIFACT_ROUTES,
    CATEGORIES,
    COMMON_SAFETY,
    GUIDES_BY_SLUG,
    LAST_REVIEWED,
    LAUNCH_GUIDES,
    guides_for_category,
)


def launch_hub(request):
    guides, active_category = guides_for_category(request.GET.get("category", "all"))
    return render(
        request,
        "gallery/launch_hub.html",
        {
            "guides": guides,
            "all_guides": LAUNCH_GUIDES,
            "categories": CATEGORIES,
            "active_category": active_category,
            "artifact_routes": ARTIFACT_ROUTES,
            "last_reviewed": LAST_REVIEWED,
        },
    )


def launch_guide(request, slug):
    guide = GUIDES_BY_SLUG.get(slug)
    if guide is None:
        raise Http404("Launch guide not found")

    related = tuple(
        candidate
        for candidate in LAUNCH_GUIDES
        if candidate["category"] == guide["category"] and candidate["slug"] != slug
    )[:3]
    return render(
        request,
        "gallery/launch_guide.html",
        {
            "guide": guide,
            "common_safety": COMMON_SAFETY,
            "related_guides": related,
            "last_reviewed": LAST_REVIEWED,
        },
    )
