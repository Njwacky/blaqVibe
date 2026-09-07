"""Backfill project history for projects published before the timeline existed.

Every published project gets one honest row — "First version published" —
dated at the project's creation time, so the project page never shows an
empty history for work that predates the feature.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    AppProject = apps.get_model('gallery', 'AppProject')
    ProjectEvent = apps.get_model('gallery', 'ProjectEvent')
    for project in AppProject.objects.filter(status='published').only('id', 'created_at'):
        if ProjectEvent.objects.filter(project_id=project.id).exists():
            continue
        event = ProjectEvent.objects.create(
            project_id=project.id,
            kind='published',
            label='First version published',
        )
        # auto_now_add stamps now(); the honest date is the project's own.
        ProjectEvent.objects.filter(pk=event.pk).update(created_at=project.created_at)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0034_projectevent_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
