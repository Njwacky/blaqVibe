# Real git daemon + admin dashboard charts: append-only clone log and the
# 'git_push' notification kind.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0023_alter_notification_kind'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(choices=[('comment', 'Comment'), ('follow', 'Follow'), ('tip', 'Tip'), ('co_owner', 'Co-owner'), ('trade', 'Trade'), ('sale', 'Sale'), ('pr', 'Pull request'), ('published', 'Published'), ('quarantined', 'Quarantined'), ('review', 'Review'), ('challenge', 'Challenge'), ('payout', 'Payout'), ('git_push', 'Git push')], max_length=20),
        ),
        migrations.CreateModel(
            name='CloneEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(choices=[('git', 'git clone/fetch'), ('zip', 'zip download')], default='zip', max_length=10)),
                ('ip_hash', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='clone_events', to='gallery.appproject')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clone_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['project', '-created_at'], name='gallery_clo_project_c4b10e_idx')],
            },
        ),
    ]
