
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0013_backfill_star_ledger'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='starevent',
            name='reason',
            field=models.CharField(choices=[('welcome', 'Welcome grant'), ('trade_spend', 'Trade — stars spent'), ('trade_earn', 'Trade — stars earned'), ('tip_spend', 'Tip — stars sent'), ('tip_earn', 'Tip — stars received'), ('challenge_bounty', 'Challenge bounty'), ('admin_adjust', 'Admin adjustment'), ('backfill', 'Ledger backfill')], max_length=20),
        ),
        migrations.CreateModel(
            name='Tip',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.PositiveIntegerField(help_text='Stars moved from sender to recipient')),
                ('message', models.CharField(blank=True, help_text='Optional note, sanitized on the way in', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tips_received', to=settings.AUTH_USER_MODEL)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tips_sent', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['recipient', '-created_at'], name='users_tip_recipie_5cace5_idx'), models.Index(fields=['sender', '-created_at'], name='users_tip_sender__30ff38_idx')],
            },
        ),
    ]
