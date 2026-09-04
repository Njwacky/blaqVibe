
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0007_pullrequest'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='appproject',
            name='ai_readme',
            field=models.TextField(blank=True, help_text='AI-generated README, backend only'),
        ),
        migrations.AddField(
            model_name='appproject',
            name='price_zar',
            field=models.PositiveIntegerField(default=0, help_text='Money price in ZAR (0=free, 50=R50) — for real money via Paystack'),
        ),
        migrations.CreateModel(
            name='Sale',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount_zar', models.PositiveIntegerField()),
                ('paystack_ref', models.CharField(blank=True, max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('buyer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales_bought', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales', to='gallery.appproject')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales_sold', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('buyer', 'project')},
            },
        ),
        migrations.CreateModel(
            name='VibeView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('count', models.PositiveIntegerField(default=1)),
                ('last_viewed', models.DateTimeField(auto_now=True)),
                ('first_viewed', models.DateTimeField(auto_now_add=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='viewer_logs', to='gallery.appproject')),
                ('viewer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vibe_views', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['project', '-last_viewed'], name='gallery_vib_project_70eaf3_idx')],
                'unique_together': {('viewer', 'project')},
            },
        ),
    ]
