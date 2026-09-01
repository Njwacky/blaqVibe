from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('gallery', '0027_appproject_static_entry_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='appreport',
            name='status',
            field=models.CharField(choices=[('open', 'Open'), ('resolved', 'Resolved'), ('ignored', 'Ignored')], db_index=True, default='open', max_length=10),
        ),
        migrations.AddField(
            model_name='appreport',
            name='outcome',
            field=models.CharField(blank=True, choices=[('', '—'), ('no_action', 'Dismissed (no violation found)'), ('quarantined', 'Vibe quarantined'), ('removed', 'Vibe removed (soft delete — buyers keep downloads)'), ('deleted', 'Vibe deleted')], default='', max_length=12),
        ),
        migrations.AddField(
            model_name='appreport',
            name='handled_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='handled_reports', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='appreport',
            name='handled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='appreport',
            name='note',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AlterModelOptions(
            name='appreport',
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='appreport',
            index=models.Index(fields=['status', 'created_at'], name='gallery_app_status_0278ed_idx'),
        ),
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(choices=[('comment', 'Comment'), ('follow', 'Follow'), ('tip', 'Tip'), ('co_owner', 'Co-owner'), ('trade', 'Trade'), ('sale', 'Sale'), ('pr', 'Pull request'), ('published', 'Published'), ('quarantined', 'Quarantined'), ('review', 'Review'), ('challenge', 'Challenge'), ('payout', 'Payout'), ('git_push', 'Git push'), ('report', 'Report')], max_length=20),
        ),
    ]
