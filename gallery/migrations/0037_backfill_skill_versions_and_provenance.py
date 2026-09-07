"""Backfill the provenance columns for data created before they existed.

Three honest fills, all derived from rows we already have — nothing is
invented:

1. Every existing Skill gets an immutable **v1** snapshot of its current
   text, so "which version was this built from?" always has an answer.
2. Every existing SkillUse points at that v1 (it is the only version that
   ever existed for that skill), and every project attached to a use gets
   `source_skill` / `source_skill_version`.
3. Every published project gets `published_at` from its own history row
   ("First version published") when there is one, otherwise from its
   creation time — the same date the project page already shows.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Skill = apps.get_model('gallery', 'Skill')
    SkillVersion = apps.get_model('gallery', 'SkillVersion')
    SkillUse = apps.get_model('gallery', 'SkillUse')
    AppProject = apps.get_model('gallery', 'AppProject')
    ProjectEvent = apps.get_model('gallery', 'ProjectEvent')

    heads = {}
    for skill in Skill.objects.all():
        head = SkillVersion.objects.filter(skill_id=skill.id).order_by('-version').first()
        if head is None:
            head = SkillVersion.objects.create(
                skill_id=skill.id,
                version=1,
                title=skill.title,
                summary=skill.summary,
                problem=skill.problem,
                workflow=skill.workflow,
                tools=skill.tools,
                expected_output=skill.expected_output,
                difficulty=skill.difficulty,
            )
            # auto_now_add stamps now(); the honest date is the skill's own.
            SkillVersion.objects.filter(pk=head.pk).update(created_at=skill.created_at)
        heads[skill.id] = head.id

    for use in SkillUse.objects.all().only('id', 'skill_id', 'project_id', 'skill_version_id'):
        version_id = heads.get(use.skill_id)
        if version_id and use.skill_version_id is None:
            SkillUse.objects.filter(pk=use.id).update(skill_version_id=version_id)
        if use.project_id:
            AppProject.objects.filter(pk=use.project_id, source_skill__isnull=True).update(
                source_skill_id=use.skill_id,
                source_skill_version_id=version_id,
            )

    for project in AppProject.objects.filter(
        status='published', published_at__isnull=True
    ).only('id', 'created_at'):
        first = (
            ProjectEvent.objects.filter(project_id=project.id, kind='published')
            .order_by('created_at')
            .values_list('created_at', flat=True)
            .first()
        )
        AppProject.objects.filter(pk=project.id).update(published_at=first or project.created_at)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0036_skill_versions_and_project_provenance'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
