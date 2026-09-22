"""Capability discovery — PROJECT → TECH → CAPABILITY → PERSON.

Rules (product standard):
1. Only published Builds count. A tag with zero projects is a claim — never shown.
2. Rank people by demonstrated project count + proof coverage, not followers/stars.
3. Every match explains itself: "Matched because…".
4. Popularity never dominates a capability query (the hardest test).
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from django.contrib.auth.models import User
from django.db.models import Q

from .ability import _stack_tokens, demonstrated_skills
from .models import AppProject

# Map raw stack / language tokens → human capability labels.
# Order matters only for display preference when multiple labels match one token.
_TOKEN_TO_CAPABILITY = {
    'python': 'Backend Development',
    'django': 'Django',
    'flask': 'Backend Development',
    'fastapi': 'API Development',
    'postgresql': 'Database Design',
    'postgres': 'Database Design',
    'mysql': 'Database Design',
    'sqlite': 'Database Design',
    'mongodb': 'Database Design',
    'redis': 'Database Design',
    'rest': 'API Development',
    'rest api': 'API Development',
    'api': 'API Development',
    'graphql': 'API Development',
    'javascript': 'Frontend Development',
    'typescript': 'Frontend Development',
    'react': 'React',
    'vue': 'Frontend Development',
    'angular': 'Frontend Development',
    'next.js': 'Frontend Development',
    'nextjs': 'Frontend Development',
    'html': 'Frontend Development',
    'css': 'Frontend Development',
    'tailwind': 'Frontend Development',
    'node': 'Backend Development',
    'nodejs': 'Backend Development',
    'node.js': 'Backend Development',
    'express': 'Backend Development',
    'java': 'Backend Development',
    'spring': 'Backend Development',
    'go': 'Backend Development',
    'golang': 'Backend Development',
    'rust': 'Backend Development',
    'c#': 'Backend Development',
    'csharp': 'Backend Development',
    '.net': 'Backend Development',
    'php': 'Backend Development',
    'laravel': 'Backend Development',
    'ruby': 'Backend Development',
    'rails': 'Backend Development',
    'swift': 'Mobile Development',
    'kotlin': 'Mobile Development',
    'flutter': 'Mobile Development',
    'react native': 'Mobile Development',
    'docker': 'DevOps',
    'kubernetes': 'DevOps',
    'aws': 'Cloud / DevOps',
    'gcp': 'Cloud / DevOps',
    'azure': 'Cloud / DevOps',
    'terraform': 'DevOps',
    'authentication': 'Authentication',
    'auth': 'Authentication',
    'oauth': 'Authentication',
    'jwt': 'Authentication',
    'machine learning': 'Machine Learning',
    'ml': 'Machine Learning',
    'tensorflow': 'Machine Learning',
    'pytorch': 'Machine Learning',
    'ai': 'AI orchestration',
    'remix / continuation': 'Remix / continuation',
    'ai orchestration': 'AI orchestration',
    'human judgment (stated)': 'Human judgment',
    'scanned / checked builds': 'Scanned builds',
}

_CAPABILITY_ALIASES = {
    'backend': 'Backend Development',
    'frontend': 'Frontend Development',
    'front-end': 'Frontend Development',
    'back-end': 'Backend Development',
    'api development': 'API Development',
    'apis': 'API Development',
    'database': 'Database Design',
    'databases': 'Database Design',
    'db': 'Database Design',
    'devops': 'DevOps',
    'mobile': 'Mobile Development',
    'auth': 'Authentication',
    'security': 'Authentication',
}


def normalize_query(raw: str) -> str:
    """Sanitize a capability / tech search string for matching."""
    text = (raw or '').strip()
    if not text:
        return ''
    try:
        import bleach
        text = bleach.clean(text, tags=[], strip=True)
    except Exception:
        pass
    # Keep letters, digits, spaces, + # . / -
    text = re.sub(r'[^\w\s+#./\-]', '', text, flags=re.UNICODE)
    return text.strip()[:80]


def capability_label_for_token(token: str) -> str | None:
    key = (token or '').strip().lower()
    if not key:
        return None
    if key in _TOKEN_TO_CAPABILITY:
        return _TOKEN_TO_CAPABILITY[key]
    if key in _CAPABILITY_ALIASES:
        return _CAPABILITY_ALIASES[key]
    # Pass through short tech names as their own capability (Django, React…).
    if 1 < len(key) <= 24 and key.replace('.', '').replace('-', '').isalnum():
        # Title-case common acronyms lightly
        if key in ('django', 'react', 'flask', 'fastapi', 'postgresql', 'mysql'):
            return key.capitalize() if key != 'postgresql' else 'PostgreSQL'
        if key == 'postgresql':
            return 'PostgreSQL'
        return token.strip()[:40]
    return None


def capabilities_for_project(project) -> list[str]:
    """Capability labels demonstrated by one published project."""
    if getattr(project, 'status', 'published') != 'published':
        return []
    labels = []
    seen = set()
    for token in _stack_tokens(project):
        label = capability_label_for_token(token)
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    # Remix / AI / judgment as capabilities when evidenced
    if project.forked_from_id:
        if 'remix / continuation' not in seen:
            labels.append('Remix / continuation')
            seen.add('remix / continuation')
    if (project.ai_tool or '').strip() or project.ai_generated:
        if 'ai orchestration' not in seen:
            labels.append('AI orchestration')
            seen.add('ai orchestration')
    if (project.human_did or '').strip():
        if 'human judgment' not in seen:
            labels.append('Human judgment')
            seen.add('human judgment')
    if project.trust in ('verified', 'scanned'):
        if 'scanned builds' not in seen:
            labels.append('Scanned builds')
            seen.add('scanned builds')
    return labels


def _project_matches_query(project, q_lower: str) -> bool:
    """True if this published project demonstrates the queried capability/tech."""
    if not q_lower:
        return False
    hay = ' '.join([
        project.tech_stack or '',
        ' '.join((project.language_stats or {}).keys()),
        project.title or '',
        project.short_description or '',
        project.problem_statement or '',
        ' '.join(capabilities_for_project(project)),
    ]).lower()
    if q_lower in hay:
        return True
    # Alias expansion
    alias = _CAPABILITY_ALIASES.get(q_lower) or _TOKEN_TO_CAPABILITY.get(q_lower)
    if alias and alias.lower() in hay:
        return True
    return False


def _proof_score(project) -> int:
    try:
        return int(project.proof_ok_count())
    except Exception:
        return 0


def search_capability(q: str, *, project_limit: int = 24, people_limit: int = 12):
    # Cache per query 60s — capability search scans 200-300 rows each hit
    try:
        from django.core.cache import cache
        q_norm = (q or '').strip().lower()[:80]
        if q_norm and len(q_norm) >= 2:
            cache_key = f"capability:search:{q_norm}:{project_limit}:{people_limit}:v1"
            hit = cache.get(cache_key)
            if hit is not None:
                return hit
    except Exception:
        cache_key = None
    _result = _search_capability_uncached(q, project_limit=project_limit, people_limit=people_limit)
    try:
        if cache_key and _result:
            cache.set(cache_key, _result, 60)
    except Exception:
        pass
    return _result


def _search_capability_uncached(q: str, *, project_limit: int = 24, people_limit: int = 12):
    """Capability-driven discovery.

    Returns:
        {
          'q': cleaned query,
          'projects': [AppProject, ...],  # matching published builds
          'people': [{
              'user', 'count', 'proof_avg', 'slugs', 'titles',
              'match_reason', 'capabilities'
          }, ...],
          'suggestions': [str, ...],  # when empty or for empty-state chips
        }

    Ranking for people: demonstrated project count DESC, then average proof
    coverage DESC. Stars and followers are intentionally ignored.
    """
    q_clean = normalize_query(q)
    suggestions = popular_capability_chips(limit=10)

    empty = {
        'q': q_clean,
        'projects': [],
        'people': [],
        'suggestions': suggestions,
    }
    if not q_clean or len(q_clean) < 2:
        return empty

    q_lower = q_clean.lower()

    # Broad SQL prefilter — keep cheap; fine match in Python.
    qs = (
        AppProject.objects.filter(status='published')
        .filter(
            Q(tech_stack__icontains=q_clean)
            | Q(title__icontains=q_clean)
            | Q(short_description__icontains=q_clean)
            | Q(problem_statement__icontains=q_clean)
            | Q(readme__icontains=q_clean)
        )
        .select_related('owner', 'owner__profile')
        .order_by('-created_at')[:200]
    )
    matched = [p for p in qs if _project_matches_query(p, q_lower)]

    # If SQL prefilter missed (e.g. language_stats only), widen once.
    if len(matched) < 3:
        wider = (
            AppProject.objects.filter(status='published')
            .select_related('owner', 'owner__profile')
            .order_by('-created_at')[:300]
        )
        seen_ids = {p.pk for p in matched}
        for p in wider:
            if p.pk in seen_ids:
                continue
            if _project_matches_query(p, q_lower):
                matched.append(p)
                seen_ids.add(p.pk)

    # Projects: proof coverage first, then recency — not stars.
    matched.sort(key=lambda p: (-_proof_score(p), -p.created_at.timestamp() if p.created_at else 0))
    projects = matched[:project_limit]

    # People buckets from matched projects only.
    buckets: dict[int, dict] = {}
    for p in matched:
        owner = p.owner
        if owner is None:
            continue
        row = buckets.setdefault(owner.pk, {
            'user': owner,
            'count': 0,
            'proof_sum': 0,
            'slugs': [],
            'titles': [],
            'caps': set(),
        })
        row['count'] += 1
        row['proof_sum'] += _proof_score(p)
        if len(row['slugs']) < 5:
            row['slugs'].append(p.slug)
            row['titles'].append(p.title)
        for cap in capabilities_for_project(p):
            row['caps'].add(cap)

    people = []
    for row in buckets.values():
        count = row['count']
        proof_avg = round(row['proof_sum'] / count, 1) if count else 0
        # Match reason — the product standard sentence.
        sample = ', '.join(f'“{t}”' for t in row['titles'][:3])
        if count == 1:
            reason = (
                f'Matched because they built 1 published project using {q_clean}'
                + (f' — {sample}' if sample else '')
                + '.'
            )
        else:
            reason = (
                f'Matched because they built {count} published projects using {q_clean}'
                + (f', including {sample}' if sample else '')
                + '.'
            )
        people.append({
            'user': row['user'],
            'count': count,
            'proof_avg': proof_avg,
            'slugs': row['slugs'],
            'titles': row['titles'],
            'match_reason': reason,
            'capabilities': sorted(row['caps'], key=str.lower)[:8],
        })

    # Hardest test: count first, proof second — never stars/followers.
    people.sort(key=lambda r: (-r['count'], -r['proof_avg'], r['user'].username.lower()))
    people = people[:people_limit]

    return {
        'q': q_clean,
        'projects': projects,
        'people': people,
        'suggestions': suggestions,
    }


def popular_capability_chips(limit: int = 10) -> list[str]:
    try:
        from django.core.cache import cache
        ck = f"capability:chips:{limit}:v1"
        hit = cache.get(ck)
        if hit is not None:
            return hit
    except Exception:
        ck = None
    _result = _popular_chips_uncached(limit)
    try:
        if ck:
            cache.set(ck, _result, 300)
    except Exception:
        pass
    return _result

def _popular_chips_uncached(limit: int = 10) -> list[str]:
    """Top tech tokens across published projects — for empty-state guidance."""
    counts: dict[str, int] = defaultdict(int)
    labels: dict[str, str] = {}
    qs = AppProject.objects.filter(status='published').only(
        'tech_stack', 'language_stats', 'status',
    )[:400]
    for p in qs:
        seen = set()
        for token in _stack_tokens(p):
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            label = capability_label_for_token(token) or token
            lk = label.lower()
            counts[lk] += 1
            labels[lk] = label
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [labels[k] for k, _ in ranked[:limit]]


def project_capability_rows(project) -> list[dict]:
    """Rows for the Build Detail 'Capabilities demonstrated' block."""
    labels = capabilities_for_project(project)
    owner = project.owner
    rows = []
    for label in labels:
        rows.append({
            'name': label,
            'owner_username': owner.username if owner else '',
            'discover_q': label,
        })
    return rows
