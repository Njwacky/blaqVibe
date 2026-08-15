"""Backfill `kind`, `preview_mode` and `appeal_score` for existing vibes.

5 Whys — why a data migration instead of a one-off script?

1. Why backfill at all? Every existing row defaults to kind='other' and
   appeal_score=0. Left alone, the whole pre-existing catalog would sort
   below any new upload and be unfilterable — the feature would look like
   it deleted the back catalog.
2. Why in a migration rather than a management command someone remembers
   to run? Deploy runs `migrate`; it does not run optional commands. A
   backfill that can be skipped will be skipped, and then production has
   two shapes of data.
3. Why heuristic only, never the LLM? A migration must be deterministic,
   offline, and finish. Calling a paid network API once per row makes it
   none of those.
4. Why iterate in chunks with bulk_update? A `.all()` over a large table
   loads it into memory; per-row saves make it N round trips.
5. Why is it reversible as a no-op? Rolling back should not wipe the
   labels — the columns themselves are dropped by 0019's reverse anyway.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    AppProject = apps.get_model('gallery', 'AppProject')
    # Import the pure logic, not the concrete models — historical models
    # are a different class, so anything touching model methods would break.
    from gallery.kind_detect import detect_kind
    from gallery.interest import compute_appeal
    from gallery.taxonomy import preview_mode_for

    qs = AppProject.objects.all().order_by('pk')
    batch = []
    for project in qs.iterator(chunk_size=200):
        try:
            verdict = detect_kind(project)
            project.kind = verdict['kind']
            project.kind_source = 'heuristic'
            project.kind_confidence = verdict.get('confidence') or 0
            project.kind_evidence = list(verdict.get('evidence') or [])[:5]
            project.preview_mode = preview_mode_for(
                project.kind,
                bool((project.html_code or '').strip()),
                bool(project.zip_file),
            )
            project.appeal_score = compute_appeal(project)
        except Exception:
            continue
        batch.append(project)
        if len(batch) >= 200:
            AppProject.objects.bulk_update(
                batch,
                ['kind', 'kind_source', 'kind_confidence', 'kind_evidence',
                 'preview_mode', 'appeal_score'],
            )
            batch = []
    if batch:
        AppProject.objects.bulk_update(
            batch,
            ['kind', 'kind_source', 'kind_confidence', 'kind_evidence',
             'preview_mode', 'appeal_score'],
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0019_kindaffinity_appproject_appeal_score_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
