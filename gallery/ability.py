"""Demonstrated ability — counted from published Builds only.

No giant Builder Score. A tag with zero projects is a claim; we do not
show those. AI maturity is a stage with evidence, not a percentage.
"""


def _stack_tokens(project):
    raw = (project.tech_stack or '') + ',' + ' '.join((project.language_stats or {}).keys())
    tokens = []
    for part in raw.replace('/', ',').replace('|', ',').split(','):
        name = part.strip()
        if not name:
            continue
        if len(name) > 40:
            continue
        tokens.append(name)
    return tokens


def demonstrated_skills(projects):
    """Return [{name, count, slugs}] sorted by count, published work only."""
    buckets = {}
    for p in projects:
        if getattr(p, 'status', 'published') != 'published':
            continue
        seen = set()
        for name in _stack_tokens(p):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            row = buckets.setdefault(key, {'name': name, 'count': 0, 'slugs': []})
            row['count'] += 1
            if len(row['slugs']) < 5:
                row['slugs'].append(p.slug)
        if p.forked_from_id:
            row = buckets.setdefault('remix', {'name': 'Remix / continuation', 'count': 0, 'slugs': []})
            row['count'] += 1
            if len(row['slugs']) < 5:
                row['slugs'].append(p.slug)
        if (p.ai_tool or '').strip() or p.ai_generated:
            row = buckets.setdefault('ai_orchestration', {'name': 'AI orchestration', 'count': 0, 'slugs': []})
            row['count'] += 1
            if len(row['slugs']) < 5:
                row['slugs'].append(p.slug)
        if (p.human_did or '').strip():
            row = buckets.setdefault('judgment', {'name': 'Human judgment (stated)', 'count': 0, 'slugs': []})
            row['count'] += 1
            if len(row['slugs']) < 5:
                row['slugs'].append(p.slug)
        if p.trust in ('verified', 'scanned'):
            row = buckets.setdefault('verified_builds', {'name': 'Scanned / checked Builds', 'count': 0, 'slugs': []})
            row['count'] += 1
            if len(row['slugs']) < 5:
                row['slugs'].append(p.slug)
    return sorted(buckets.values(), key=lambda r: (-r['count'], r['name'].lower()))


AI_STAGES = (
    (1, 'Prompter', 'Published a Build.'),
    (2, 'Iterator', 'Named the AI tool and left a workflow note.'),
    (3, 'Debugger', 'Documented what AI got wrong.'),
    (4, 'Verifier', 'A scanned/checked Build exists.'),
    (5, 'Designer', 'Stated the problem and what the human did.'),
    (6, 'Orchestrator', 'Several AI-assisted Builds, not a one-off.'),
    (7, 'Continuer', 'Remixed with a stated delta — improved an existing thing.'),
)


def ai_maturity(projects):
    """Highest stage this person has *demonstrated*, plus the evidence flags."""
    published = [p for p in projects if getattr(p, 'status', 'published') == 'published']
    flags = {
        'published': bool(published),
        'named_tool': any((p.ai_tool or '').strip() or p.ai_generated for p in published),
        'workflow_note': any((p.ai_prompt or '').strip() for p in published),
        'ai_wrong': any((p.ai_got_wrong or '').strip() for p in published),
        'scanned': any(p.trust in ('verified', 'scanned') for p in published),
        'problem_and_human': any(
            (p.problem_statement or '').strip() and (p.human_did or '').strip()
            for p in published
        ),
        'ai_volume': sum(1 for p in published if (p.ai_tool or '').strip() or p.ai_generated) >= 3,
        'remix_delta': any(
            p.forked_from_id and (p.remix_changed or '').strip() for p in published
        ),
    }
    stage = 0
    if flags['published']:
        stage = 1
    if flags['named_tool'] and flags['workflow_note']:
        stage = 2
    if flags['ai_wrong']:
        stage = 3
    if flags['scanned'] and stage >= 1:
        stage = max(stage, 4)
    if flags['problem_and_human']:
        stage = max(stage, 5)
    if flags['ai_volume']:
        stage = max(stage, 6)
    if flags['remix_delta']:
        stage = max(stage, 7)
    label = 'No published Builds yet'
    blurb = 'Ability is invisible until there is an object.'
    for number, name, text in AI_STAGES:
        if number == stage:
            label = name
            blurb = text
            break
    return {
        'stage': stage,
        'label': label,
        'blurb': blurb,
        'flags': flags,
        'stages': AI_STAGES,
    }
