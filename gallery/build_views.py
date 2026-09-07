"""DISCOVER and BUILD — two of the five primary destinations (§4).

**Discover** answers "what is happening on BlaqVibes?" without asking the
visitor to filter anything: what people are building now, which ideas are
being remixed, which families are growing, who remixes most. It is the
social half of the core loop — DISCOVER → REMIX → CHANGE → PUBLISH →
ORIGINAL CREATOR SEES IT → MORE DISCOVERY (§3).

**Build** is the workflow entry point (§7):

    BUILD SOMETHING
      ├── Start from scratch
      ├── Remix a project
      └── Use a Builder Skill
            ↓ CREATE → UPLOAD → SHOW → FEEDBACK → IMPROVE → PUBLISH

Both pages are read-only and crush-safe: a failing rail renders an honest
empty state instead of a 500.
"""
import logging

from django.db.models import Count, Q
from django.shortcuts import render

from . import remix_stats, trending
from .daily import today_challenge
from .models import AppProject
from .skill_models import Skill, SkillUse

logger = logging.getLogger(__name__)

BUILD_STEPS = [
    ('CREATE', 'Make the thing — by hand, with AI as a tool, or by remixing.'),
    ('UPLOAD', 'Bring the files in. Every upload is scanned before the feed.'),
    ('SHOW', 'Publish the story: what it does, how it was built, what is inside.'),
    ('FEEDBACK', 'Builders comment, review and star. Real reactions, not vanity.'),
    ('IMPROVE', 'Ship a new version — the project history records what changed.'),
    ('PUBLISH', 'Your project becomes something other builders can remix.'),
]


def discover(request):
    """The community page: projects, people, and how ideas travel."""
    ctx = {
        'totals': {'projects': 0, 'originals': 0, 'remixes': 0, 'deepest_generation': 0, 'remix_share': 0},
        'trending': [],
        'trending_is_hot': False,
        'fresh_remixes': [],
        'most_remixed': [],
        'growing_families': [],
        'top_remixers': [],
        'rising_creators': [],
        'new_projects': [],
        'top_skills': [],
        'activity': None,
        'daily': None,
    }
    try:
        exclude_owner = request.user if request.user.is_authenticated else None
        ctx['totals'] = remix_stats.remix_totals()
        ctx['trending'], ctx['trending_is_hot'] = trending.trending_vibes(
            limit=6, exclude_owner=exclude_owner)
        ctx['fresh_remixes'] = remix_stats.fresh_remixes(limit=6)
        ctx['most_remixed'] = remix_stats.most_remixed(limit=6)
        ctx['growing_families'] = remix_stats.fastest_growing_families(limit=4)
        ctx['top_remixers'] = remix_stats.top_remixers(limit=6)
        ctx['rising_creators'] = trending.rising_creators(
            limit=4, exclude_user=exclude_owner)
        ctx['new_projects'] = list(
            AppProject.objects.filter(status='published')
            .select_related('owner', 'owner__profile')
            .annotate(remix_count=Count('forks', filter=Q(forks__status='published')))
            .order_by('-created_at')[:6]
        )
        ctx['top_skills'] = list(
            Skill.objects.filter(is_published=True)
            .select_related('creator')
            .order_by('-projects_created', '-uses')[:4]
        )
        ctx['activity'] = trending.activity_summary()
        ctx['daily'] = today_challenge()
    except Exception:
        logger.exception('discover rails failed')
    return render(request, 'gallery/discover.html', ctx)


def build_hub(request):
    """BUILD — one page, three honest ways to start something."""
    ctx = {
        'steps': BUILD_STEPS,
        'remixable': [],
        'skills': [],
        'pending_skill_use': None,
        'my_drafts': [],
        'daily': None,
    }
    try:
        ctx['remixable'] = remix_stats.remixable_projects(request.user, limit=6)
        ctx['skills'] = list(
            Skill.objects.filter(is_published=True)
            .select_related('creator')
            .order_by('-projects_created', '-uses')[:4]
        )
        ctx['daily'] = today_challenge()
        if request.user.is_authenticated:
            # A skill the builder just picked up but has not yet turned into
            # a project — the open half of SKILL → BUILD → PROJECT → PROOF.
            ctx['pending_skill_use'] = (
                SkillUse.objects.filter(user=request.user, project__isnull=True)
                .select_related('skill', 'skill_version')
                .order_by('-created_at')
                .first()
            )
            ctx['my_drafts'] = list(
                AppProject.objects.filter(owner=request.user)
                .exclude(status='published')
                .order_by('-created_at')[:3]
            )
    except Exception:
        logger.exception('build hub rails failed')
    return render(request, 'gallery/build.html', ctx)
