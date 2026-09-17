"""Give every notification that predates severity its correct colour.

`category` ships with a default of 'system' — the grey stripe that means
"nothing to do". Without this pass, an existing quarantined build would sit at
the BOTTOM of the inbox in grey next to a "your project is live" row, which is
the exact inversion the feature exists to prevent.

The map is copied inline rather than imported from gallery.notify: a migration
must describe the past with the code as it was, and importing live application
code means a later edit to the map would silently rewrite history on the next
`migrate`. The two are kept in step by
gallery.test_attention.NotificationCategoryTests.test_migration_map_matches_live_map.
"""
from django.db import migrations

CATEGORY_OF_KIND = {
    'quarantined': 'critical',
    'account_quarantine': 'critical',
    'appeal': 'critical',
    'git_push_rejected': 'critical',
    'duplicate': 'action',
    'malfunction': 'action',
    'approval': 'action',
    'pending': 'action',
    'review': 'action',
    'review_needed': 'action',
    'challenge_draft': 'action',
    'pr': 'action',
    'co_owner': 'action',
    'report': 'action',
    'tip': 'money',
    'trade': 'money',
    'sale': 'money',
    'payout': 'money',
    'comment': 'social',
    'follow': 'social',
    'star': 'social',
    'fork': 'social',
    'challenge': 'social',
    'feedback': 'social',
    'published': 'system',
    'upload': 'system',
    'git_push': 'system',
    'milestone': 'system',
    'achievement': 'system',
    'role': 'system',
}


def backfill(apps, schema_editor):
    Notification = apps.get_model('gallery', 'Notification')
    for kind, category in CATEGORY_OF_KIND.items():
        # One UPDATE per category, not per row: an inbox table is the largest
        # table in this database and a migrate that loops over it row by row
        # would run for minutes on production.
        Notification.objects.filter(kind=kind).exclude(category=category).update(category=category)


def noop(apps, schema_editor):
    # Reversing would set every row back to 'system' — destroying a fact the
    # live code derives on write anyway. Leaving it is the honest no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0045_notification_category_notification_reminded_at_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
