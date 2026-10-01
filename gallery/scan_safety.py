"""Short evidence-write guards for database scan claims.

Scans hold no row locks while running subprocesses or reading storage. Every
safety write is checked against the claim and the immutable content snapshot,
so a removed project or a replacement upload cannot inherit an old verdict.
"""
from contextlib import contextmanager

from django.db import transaction


class ScanSuperseded(Exception):
    """The project or queue claim changed while its checks were running."""


@contextmanager
def scan_project(project_id, claim=None):
    from .models import AppProject, ScanJob

    if claim is None:
        # Optional Celery retains its existing task contract.
        yield AppProject.objects.get(pk=project_id)
        return
    with transaction.atomic():
        project = AppProject.objects.select_for_update().get(pk=project_id)
        job = ScanJob.objects.select_for_update().get(pk=claim['job_id'], project_id=project_id)
        archive = project.zip_file.name if project.zip_file else ''
        if (job.task_id != claim['task_id'] or job.status != 'scanning'
                or project.status not in ('pending', 'quarantined')
                or archive != claim['archive']
                or (not archive and (project.html_code, project.css_code, project.js_code) != claim['snippet'])):
            raise ScanSuperseded()
        yield project
