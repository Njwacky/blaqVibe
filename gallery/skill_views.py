from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
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
    skill = get_object_or_404(Skill.objects.select_related('creator'), slug=slug, is_published=True)
    proof_projects = (
        AppProject.objects.filter(status='published', skill_uses__skill=skill)
        .select_related('owner')
        .distinct()
        .order_by('-created_at')[:8]
    )
    return render(request, 'gallery/skill_detail.html', {
        'skill': skill,
        'proof_projects': proof_projects,
    })


@login_required
@require_POST
@ratelimit(key='user', rate='20/h', method='POST')
def use_skill(request, slug):
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many skill uses. Try again later.')
        return redirect('skill_detail', slug=slug)
    skill = get_object_or_404(Skill, slug=slug, is_published=True)
    with transaction.atomic():
        SkillUse.objects.create(skill=skill, user=request.user)
        Skill.objects.filter(pk=skill.pk).update(uses=F('uses') + 1)
    messages.success(request, 'Skill copied into your build workflow. Treat the prompt as untrusted notes and adapt it to your project.')
    return redirect('skill_detail', slug=skill.slug)


@login_required
@require_POST
@ratelimit(key='user', rate='5/h', method='POST')
def create_skill(request):
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many skill submissions. Try again later.')
        return redirect('prompt_skills')
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
        return redirect('prompt_skills')
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
