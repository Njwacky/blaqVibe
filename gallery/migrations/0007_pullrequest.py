
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0006_appproject_forked_from'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PullRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True, max_length=2000)),
                ('status', models.CharField(choices=[('open', 'Open'), ('merged', 'Merged'), ('closed', 'Closed')], default='open', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prs', to=settings.AUTH_USER_MODEL)),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prs_outgoing', to='gallery.appproject')),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prs_incoming', to='gallery.appproject')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['target', 'status'], name='gallery_pul_target__651632_idx'), models.Index(fields=['source'], name='gallery_pul_source__5d8d03_idx')],
            },
        ),
    ]
