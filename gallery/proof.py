"""Publish-first proof — PUBLISH → then strengthen.

The publish flow collects the minimum (name, what-it-is, files, build
method). Everything that makes a project more *provable* — a real README,
AI details, human contribution, skills — is an OPTIONAL next step, surfaced
here as honest, actionable rows instead of required form fields.

Two rules shape this module:

1. Never block publishing. Nothing in here raises or validates.
2. Never fake completion. A scaffold README is not a real README; the
   strengthen list keeps showing it until the creator writes their own.
"""
from django.urls import reverse


def scaffold_readme(title, short_description=''):
    """The starter README written on the creator's behalf at publish time.

    It is deliberately honest about being a scaffold — the last line says so
    in public — and `AppProject.has_scaffold_readme` detects it (exact match)
    so the creator keeps being offered "make this yours" until they do.
    """
    title = (title or 'Untitled project').strip() or 'Untitled project'
    desc = (short_description or '').strip()
    parts = [f'# {title}', '']
    if desc:
        parts += [desc, '']
    parts += [
        '## About this build',
        '',
        'Published on BlaqVibes.',
        '',
        'This README is a starter scaffold — the full story (what it does, '
        'how to run it, what the builder learned) is added by the creator.',
        '',
    ]
    return '\n'.join(parts)


def claims_ai(project):
    """Single source of truth for "did the creator say AI was involved".

    Mirrors AppProject.claims_ai; kept as a function so views can ask about
    unsaved instances the same way the model answers for saved ones.
    """
    return project.claims_ai


def strengthen_actions(project):
    """The optional next steps that make this project's proof stronger.

    Returns a list of {'label', 'url', 'note'} — every destination is a real
    page that already exists (edit sections, Builder Skills). Rows with
    url=None are informational (the platform does them automatically) and are
    rendered as status lines, never as dead buttons.
    """
    actions = []
    edit_url = reverse('edit_vibe', args=[project.slug])

    # README — a scaffold is a start, not a story.
    if project.has_scaffold_readme:
        actions.append({
            'label': 'Add a real README',
            'url': f'{edit_url}#sec-readme',
            'note': 'What it does, how to run it.',
        })

    # AI transparency — details are invited, never demanded.
    if project.claims_ai:
        if not (project.ai_tool or '').strip():
            actions.append({
                'label': 'Name your AI tool',
                'url': f'{edit_url}#sec-build',
                'note': 'One field — Claude, Gemini, Cursor…',
            })
        if not (project.ai_prompt or '').strip():
            actions.append({
                'label': 'Add AI details',
                'url': f'{edit_url}#sec-build',
                'note': 'How did AI help? A sentence is enough.',
            })
        if not (project.human_did or '').strip():
            actions.append({
                'label': 'Add what you did',
                'url': f'{edit_url}#sec-build',
                'note': 'Your judgment is the scarce proof.',
            })
    elif project.build_method == 'human_built':
        actions.append({
            'label': 'Explain how AI helped (optional)',
            'url': f'{edit_url}#sec-build',
            'note': 'Skip it — human-built needs no explanation.',
        })

    # Remix delta — credit plus change.
    if project.forked_from_id and not (project.remix_changed or '').strip():
        actions.append({
            'label': 'Describe your remix',
            'url': f'{edit_url}#sec-build',
            'note': 'What changed from the original?',
        })

    # Presentation — a thumbnail travels better in the feed.
    if not project.thumbnail:
        actions.append({
            'label': 'Add a thumbnail',
            'url': f'{edit_url}#sec-basics',
            'note': 'One image makes the card pop.',
        })

    # Skills — a user-level proof layer, kept as an invite.
    actions.append({
        'label': 'Publish a Builder Skill',
        'url': reverse('create_skill'),
        'note': 'Turn how you built this into something others can use.',
    })

    return actions


def proof_level(ok_count, total):
    """(key, title) for the proof meter — neutral words, no shaming."""
    if total <= 0:
        return ('basic', 'Basic')
    ratio = ok_count / total
    if ok_count >= total - 1 or ratio >= 0.9:
        return ('strong', 'Strong')
    if ratio >= 0.6:
        return ('solid', 'Solid')
    return ('basic', 'Basic')
