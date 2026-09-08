"""Phase 4 — opportunity as a side effect of the object.

Matching is via published Builds, not talent profiles.
A Proof CV is generated from work. There is no Jobs tab.
"""
import re
from collections import Counter

from django.db.models import Count, Q

from .ability import ai_maturity, demonstrated_skills
from .models import AppProject

_STOP = {
    'the', 'a', 'an', 'and', 'or', 'to', 'of', 'for', 'in', 'on', 'with',
    'without', 'this', 'that', 'from', 'into', 'is', 'are', 'was', 'be',
    'it', 'its', 'my', 'our', 'your', 'their', 'by', 'at', 'as', 'not',
}


def tokens(text):
    words = re.findall(r'[a-z0-9]{3,}', (text or '').lower())
    return {w for w in words if w not in _STOP}


def project_tokens(project):
    return tokens(' '.join([
        project.problem_statement or '',
        project.title or '',
        project.tech_stack or '',
    ]))


def similar_builds(project, pool, limit=5):
    """Other published Builds that share problem/stack words."""
    mine = tokens(project.problem_statement or '') | tokens(project.title or '')
    if len(mine) < 2:
        return []
    scored = []
    for other in pool:
        if other.pk == project.pk:
            continue
        if getattr(other, 'status', 'published') != 'published':
            continue
        theirs = tokens(other.problem_statement or '') | tokens(other.title or '')
        overlap = mine & theirs
        if len(overlap) < 2:
            continue
        scored.append((len(overlap), other))
    scored.sort(key=lambda row: (-row[0], -row[1].stars))
    return [p for _, p in scored[:limit]]


def open_problems(limit=12):
    """Published Builds that named a problem and still have room to remix."""
    qs = (
        AppProject.objects.filter(status='published')
        .exclude(problem_statement='')
        .select_related('owner')
        .annotate(remixes=Count('forks', filter=Q(forks__status='published')))
        .order_by('remixes', '-created_at')
    )
    return list(qs[:limit])


def proof_cv(user, projects):
    """Shareable record generated only from published work."""
    published = [p for p in projects if getattr(p, 'status', 'published') == 'published']
    problems = [p for p in published if (p.problem_statement or '').strip()]
    remixes = [p for p in published if p.forked_from_id]
    remixed_by = 0
    for p in published:
        remixed_by += getattr(p, 'remixes', None) or 0
    return {
        'user': user,
        'builds': published,
        'problems': problems,
        'remix_count': len(remixes),
        'original_count': len(published) - len(remixes),
        'demonstrated': demonstrated_skills(published),
        'maturity': ai_maturity(published),
        'stars': sum(p.stars for p in published),
        'witness_reviews': sum(p.review_count for p in published),
    }
