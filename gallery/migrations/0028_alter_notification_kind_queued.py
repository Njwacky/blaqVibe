# Waiting-for-approval inbox note (kind='queued') so an upload that is
# sitting in scan/moderation is not silent.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0027_appproject_static_entry_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(
                choices=[
                    ('comment', 'Comment'),
                    ('follow', 'Follow'),
                    ('tip', 'Tip'),
                    ('co_owner', 'Co-owner'),
                    ('trade', 'Trade'),
                    ('sale', 'Sale'),
                    ('pr', 'Pull request'),
                    ('published', 'Published'),
                    ('queued', 'Waiting for approval'),
                    ('quarantined', 'Quarantined'),
                    ('review', 'Review'),
                    ('challenge', 'Challenge'),
                    ('payout', 'Payout'),
                    ('git_push', 'Git push'),
                ],
                max_length=20,
            ),
        ),
    ]
