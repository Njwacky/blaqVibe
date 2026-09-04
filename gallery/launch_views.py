"""Public, read-only launch guidance views."""

import logging
from datetime import date

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
from .framework_commands import framework_commands_for_guide
from .comparison import enrich_comparison_groups

logger = logging.getLogger(__name__)

REVIEW_MAX_AGE_DAYS = 90


def _review_status(guide):
    """Days since a guide's last review and whether it is now stale.

    5 Whys:
    1. Why per-guide dates instead of the global LAST_REVIEWED? The global
       date says "something was reviewed on that day" — it cannot tell a
       reader whether THIS guide's commands and policies were checked.
    2. Why compute in the view instead of at import time? Launch pages are
       public and read-only; a few date subtractions per request are free,
       and the value is always fresh without a cache flush after a review.
    3. Why ISO dates in the data? date.fromisoformat parses them with no
       ambiguity, and a reviewer writing 2026-08-20 cannot be misread.
    4. Why fail soft? A missing/unparseable date means "unknown age", which
       is worse than stale — the reader should see the guide was never
       tracked. The management command is the hard gate.
    5. Why 90 days? The maintenance checklist promises a quarterly review;
       anything older than a quarter is by definition overdue.
    """
    raw = guide.get("last_reviewed", "")
    if not raw:
        return {"days_since": None, "is_outdated": True, "missing": True}
    try:
        reviewed = date.fromisoformat(raw)
    except ValueError:
        logger.warning("Guide %s has unparseable last_reviewed %r", guide.get("slug"), raw)
        return {"days_since": None, "is_outdated": True, "missing": True}
    days = (date.today() - reviewed).days
    return {"days_since": days, "is_outdated": days > REVIEW_MAX_AGE_DAYS, "missing": False}


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
    return {**guide, "steps": steps, "review": _review_status(guide)}


def launch_hub(request):
    guides, active_category = guides_for_category(request.GET.get("category", "all"))
    guides = tuple(_annotate_guide(guide) for guide in guides)

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
            "comparison_groups": enrich_comparison_groups(GUIDES_BY_SLUG),
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
            "framework_commands": framework_commands_for_guide(guide),
        },
    )
