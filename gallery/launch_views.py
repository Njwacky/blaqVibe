"""Public, read-only launch guidance views."""

import logging

from django.http import Http404
from django.shortcuts import render

from .launch_guides import (
    ARTIFACT_GROUPS,
    ARTIFACT_ROUTES,
    CATEGORIES,
    COMMON_SAFETY,
    GUIDES_BY_SLUG,
    LAST_REVIEWED,
    LAUNCH_GUIDES,
    guides_for_category,
)

logger = logging.getLogger(__name__)

# Markers taken from official-doc claims already in the guides. Used only to
# surface a visual "high-stakes" flag — the step copy itself is unchanged.
_HIGH_RISK_MARKERS = (
    "secret",
    "private key",
    "credential",
    "production deployment",
    "first deployment",
    "do not assume",
    "14 days",
    "30-day",
    "notar",
    "trusted root",
    "fee",
    "submit for review",
    "add for review",
)


def _step_is_high_risk(step):
    blob = f"{step.get('title', '')} {step.get('body', '')}".lower()
    return any(marker in blob for marker in _HIGH_RISK_MARKERS)


def _annotate_guide(guide):
    steps = tuple({**step, "high_risk": _step_is_high_risk(step)} for step in guide["steps"])
    return {**guide, "steps": steps}


def launch_hub(request):
    guides, active_category = guides_for_category(request.GET.get("category", "all"))

    requested_artifact = (request.GET.get("artifact") or "").strip()
    active_artifact = ""
    invalid_artifact = ""
    matching_slugs = ()
    match_count = 0
    active_route = None

    if requested_artifact:
        for route in ARTIFACT_ROUTES:
            if route["value"] == requested_artifact:
                active_artifact = requested_artifact
                matching_slugs = route["guides"]
                active_route = route
                break
        else:
            invalid_artifact = requested_artifact
            logger.info("Unknown launch artifact requested: %s", requested_artifact)

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
            "artifact_groups": ARTIFACT_GROUPS,
            "active_artifact": active_artifact,
            "active_route": active_route,
            "invalid_artifact": invalid_artifact,
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
            "guide": _annotate_guide(guide),
            "common_safety": COMMON_SAFETY,
            "related_guides": related,
            "last_reviewed": LAST_REVIEWED,
        },
    )
