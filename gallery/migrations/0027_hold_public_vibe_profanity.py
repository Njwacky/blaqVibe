"""Hold already-published vibes whose public text fails the language gate.

The write gates (AppUploadForm, AppProject.save) stop NEW dirty rows.
Rows created before the gate — via shell, admin, or older code — may
already carry blocked words in a published title, README, or description.

We do NOT rewrite the author's text (silent rewrites hide what happened
and teach nobody anything). The rows are demoted to 'pending' with a
scan_report note, so:
  * they disappear from the feed and the public API immediately,
  * the raw text stays visible to moderators in the queue,
  * the owner's reworded edit re-publishes through the normal path.

Dirty slugs (slugified from a dirty title) are replaced with a neutral
unique slug — a URL is a public surface too. Dirty version changelogs
fall back to 'Update', matching the new AppVersion.save backstop.
"""

from django.db import migrations
from django.utils import timezone

PUBLIC_FIELDS = ('title', 'readme', 'short_description', 'tech_stack')


def _dirty_fields(apps, row):
    from gallery.profanity import contains_profanity
    return [
        name for name in PUBLIC_FIELDS
        if contains_profanity(getattr(row, name, '') or '')
    ]


def _hold(apps, schema_editor):
    from gallery.profanity import contains_profanity

    AppProject = apps.get_model('gallery', 'AppProject')
    now = timezone.now().isoformat()

    for row in AppProject.objects.all().iterator():
        changed = []

        dirty = _dirty_fields(apps, row)
        if dirty:
            if row.status == 'published':
                row.status = 'pending'
                changed.append('status')
            report = dict(row.scan_report or {})
            report['language_gate'] = {
                'fields': dirty,
                'at': now,
                'note': 'Blocked language in public text — held from the feed until reworded.',
            }
            row.scan_report = report
            changed.append('scan_report')

        # A slug minted from a blocked title is a blocked word in a URL.
        if row.slug and contains_profanity(row.slug.replace('-', ' ')):
            base = 'vibe'
            slug = base
            i = 1
            while AppProject.objects.filter(slug=slug).exclude(pk=row.pk).exists():
                slug = f'{base}-{i}'
                i += 1
            row.slug = slug
            changed.append('slug')

        if changed:
            row.save(update_fields=changed)

    AppVersion = apps.get_model('gallery', 'AppVersion')
    for row in AppVersion.objects.all().iterator():
        if contains_profanity(row.changelog):
            row.changelog = 'Update'
            row.save(update_fields=['changelog'])


def _noop(apps, schema_editor):
    # Irreversible on purpose: we will not put the words back.
    return


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0026_commentreport_and_moderation_kind'),
    ]

    operations = [
        migrations.RunPython(_hold, _noop),
    ]
