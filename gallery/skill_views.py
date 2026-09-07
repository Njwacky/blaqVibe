from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .skill_models import Skill, SkillUse
from .models import AppProject
from .prompt_sanitize import sanitize_prompt


def _clean(value, limit):
    return sanitize_prompt((value or '').strip())[:limit]


def skill_list(request):
    q = _clean(request.GET.get('q'), 100)
    difficulty = request.GET.get('difficulty', '').strip().lower()
    if difficulty not in {'beginner', 'intermediate', 'advanced'}:
        difficulty = ''
    skills = Skill.objects.filter(is_published=True).select_related('creator')
    if q:
        skills = skills.filter(title__icontains=q) | skills.filter(summary__icontains=q)
    if difficulty:
        skills = skills.filter(difficulty=difficulty)
    skills = skills.order_by('-uses', '-stars', '-created_at')[:60]
    return render(request, 'gallery/skills.html', {
        'skills': skills,
        'q': q,
        'difficulty': difficulty,
    })


def skill_detail(request, slug):
    """SKILL → SKILL VERSION → USE → BUILD → PROJECT → PROOF (§16).

    The page shows the living workflow, the immutable version history
    behind it, and the published projects that prove the skill works.
    Proof counts published projects only — a pending upload is not proof.
    """
    skill = get_object_or_404(Skill.objects.select_related('creator'), slug=slug, is_published=True)
    proof_projects = (
        AppProject.objects.filter(status='published')
        .filter(Q(skill_uses__skill=skill) | Q(source_skill=skill))
        .select_related('owner', 'source_skill_version')
        .distinct()
        .order_by('-created_at')[:8]
    )
    proof_count = (
        AppProject.objects.filter(status='published')
        .filter(Q(skill_uses__skill=skill) | Q(source_skill=skill))
        .distinct()
        .count()
    )
    versions = list(skill.versions.order_by('-version')[:10])
    builders_used = (
        SkillUse.objects.filter(skill=skill).values('user_id').distinct().count()
    )
    return render(request, 'gallery/skill_detail.html', {
        'skill': skill,
        'proof_projects': proof_projects,
        'proof_count': proof_count,
        'versions': versions,
        'current_version': versions[0] if versions else None,
        'builders_used': builders_used,
        'is_author': request.user.is_authenticated and request.user.pk == skill.creator_id,
    })


@login_required
@require_POST
@ratelimit(key='user', rate='20/h', method='POST')
def use_skill(request, slug):
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many skill uses. Try again later.')
        return redirect('skill_detail', slug=slug)
    skill = get_object_or_404(Skill, slug=slug, is_published=True)
    version = skill.current_version
    with transaction.atomic():
        SkillUse.objects.create(skill=skill, skill_version=version, user=request.user)
        Skill.objects.filter(pk=skill.pk).update(uses=F('uses') + 1)
    messages.success(
        request,
        'Skill added to your workflow — go build. The next project you publish '
        'within 2 hours is linked to this skill'
        + (f' ({version.label})' if version else '')
        + ' as proof. Treat the workflow as untrusted notes and adapt it to '
        'your project.',
    )
    # §8: a skill leads into creation, not back to a reading page.
    # Skill → Start Building → Project → Built using Skill X → proof.
    return redirect(f"{reverse('build_hub')}?skill={skill.slug}")


@login_required
@require_POST
@ratelimit(key='user', rate='10/h', method='POST')
def update_skill(request, slug):
    """Edit a skill you published — the edit publishes a NEW version.

    Nothing is overwritten in history: earlier versions stay readable and
    the projects built from them keep pointing at the exact text they
    used. That is what makes "built from v1" evidence instead of a label.
    """
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many edits. Try again later.')
        return redirect('skill_detail', slug=slug)
    skill = get_object_or_404(Skill, slug=slug, creator=request.user)
    fields = {
        'title': (140, request.POST.get('title')),
        'summary': (260, request.POST.get('summary')),
        'problem': (1000, request.POST.get('problem')),
        'workflow': (5000, request.POST.get('workflow')),
        'tools': (300, request.POST.get('tools')),
        'expected_output': (500, request.POST.get('expected_output')),
    }
    for name, (limit, raw) in fields.items():
        if raw is not None:
            setattr(skill, name, _clean(raw, limit))
    difficulty = (request.POST.get('difficulty') or skill.difficulty).strip().lower()
    if difficulty in {'beginner', 'intermediate', 'advanced'}:
        skill.difficulty = difficulty
    if not skill.title or not skill.summary or not skill.workflow:
        messages.error(request, 'A skill needs a title, a summary and a workflow.')
        return redirect('skill_detail', slug=slug)
    skill.save()
    version = skill.snapshot_version()
    messages.success(
        request,
        f'Skill updated — published as {version.label}. Earlier versions stay '
        'readable, so projects built from them keep their proof.',
    )
    return redirect('skill_detail', slug=skill.slug)


@login_required
@require_POST
@ratelimit(key='user', rate='5/h', method='POST')
def create_skill(request):
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many skill submissions. Try again later.')
        return redirect('skills')
    title = _clean(request.POST.get('title'), 140)
    summary = _clean(request.POST.get('summary'), 260)
    problem = _clean(request.POST.get('problem'), 1000)
    workflow = _clean(request.POST.get('workflow'), 5000)
    tools = _clean(request.POST.get('tools'), 300)
    expected_output = _clean(request.POST.get('expected_output'), 500)
    tags = _clean(request.POST.get('tags'), 300)
    difficulty = request.POST.get('difficulty', 'beginner').strip().lower()
    errors = []
    if not title: errors.append('Give the skill a title.')
    if not summary: errors.append('Explain the result in one short sentence.')
    if not problem: errors.append('Describe the problem this skill solves.')
    if not workflow: errors.append('Add the reusable workflow or prompt.')
    if difficulty not in {'beginner', 'intermediate', 'advanced'}:
        errors.append('Choose a valid difficulty.')
    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect('skills')
    skill = Skill(
        creator=request.user,
        title=title,
        summary=summary,
        problem=problem,
        workflow=workflow,
        tools=tools,
        difficulty=difficulty,
        expected_output=expected_output,
        tags=tags,
    )
    skill.save()
    messages.success(request, 'Skill published. Now let builders prove it with projects.')
    return redirect(skill.get_absolute_url())
