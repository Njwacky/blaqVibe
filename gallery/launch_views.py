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

    active_artifact = request.GET.get("artifact", "")
    matching_slugs = ()
    match_count = 0
    if active_artifact:
        for route in ARTIFACT_ROUTES:
            if route["value"] == active_artifact:
                matching_slugs = route["guides"]
                break
        else:
            # Unknown input safely falls back to no selection.
            active_artifact = ""
        if active_artifact:
            match_count = sum(1 for guide in guides if guide["slug"] in matching_slugs)

    return render(
        request,
        "gallery/launch_hub.html",
        {
            "guides": guides,
            "all_guides": LAUNCH_GUIDES,
            "categories": CATEGORIES,
            "active_category": active_category,
            "artifact_routes": ARTIFACT_ROUTES,
            "active_artifact": active_artifact,
            "matching_slugs": matching_slugs,
            "match_count": match_count,
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
