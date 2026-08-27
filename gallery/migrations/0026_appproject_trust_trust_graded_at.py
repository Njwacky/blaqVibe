from django.db import migrations, models
import django.utils.timezone


def backfill_trust(apps, schema_editor):
    """Grade existing published rows from the evidence already stored.

    4 points on why a backfill at all: (1) the badge must not be empty
    for the whole catalog on launch day — that would teach users it never
    appears; (2) every pre-existing published row DID go through the
    pipeline (publish requires it), so most have evidence in scan_report;
    (3) rows without evidence grade to 'unknown', which renders no badge —
    the honest answer for legacy rows; (4) this is a one-shot derivation
    of the same pure function the pipeline uses, so backfill and runtime
    can never disagree. If it fails on any row → that row is skipped and
    left 'unknown' (degrade, never block the deploy).
    """
    AppProject = apps.get_model('gallery', 'AppProject')
    from gallery.trust import apply_trust_grade

    for row in AppProject.objects.filter(status='published').iterator():
        try:
            apply_trust_grade(row)
        except Exception:
            pass


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0025_scrub_public_profanity'),
    ]

    operations = [
        migrations.AddField(
            model_name='appproject',
            name='trust',
            field=models.CharField(
                choices=[
                    ('verified', 'Verified — virus clean, no secrets, deps checked'),
                    ('scanned', 'Scanned — pipeline ran, some checks incomplete'),
                    ('unknown', 'Unknown — no complete scan evidence'),
                ],
                db_index=True,
                default='unknown',
                help_text='verified | scanned | unknown — pipeline-written only, see gallery.trust.',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='appproject',
            name='trust_graded_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the trust tier was last written (grade or reset).',
                null=True,
            ),
        ),
        migrations.RunPython(backfill_trust, migrations.RunPython.noop),
    ]
