
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0017_profile_canvas_url'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='last_rename_at',
            field=models.DateTimeField(blank=True, help_text='Set by rename_user — the 30-day cooldown anchor', null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='name_color',
            field=models.CharField(default='default', max_length=20),
        ),
        migrations.AddField(
            model_name='profile',
            name='name_font',
            field=models.CharField(default='classic', max_length=20),
        ),
        migrations.AddField(
            model_name='profile',
            name='name_fx',
            field=models.CharField(default='none', max_length=20),
        ),
        migrations.AddField(
            model_name='profile',
            name='name_size',
            field=models.CharField(default='md', max_length=4),
        ),
        migrations.AlterField(
            model_name='starevent',
            name='reason',
            field=models.CharField(choices=[('welcome', 'Welcome grant'), ('trade_spend', 'Trade — stars spent'), ('trade_earn', 'Trade — stars earned'), ('tip_spend', 'Tip — stars sent'), ('tip_earn', 'Tip — stars received'), ('challenge_bounty', 'Challenge bounty'), ('admin_adjust', 'Admin adjustment'), ('backfill', 'Ledger backfill'), ('payout_hold', 'Payout — stars held for cash-out'), ('payout_refund', 'Payout — rejected, stars returned'), ('rename_spend', 'Rename — rename card burned'), ('style_spend', 'Name style — cosmetic burned')], max_length=20),
        ),
        migrations.CreateModel(
            name='UsernameHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('old_username', models.CharField(db_index=True, max_length=150)),
                ('new_username', models.CharField(max_length=150)),
                ('method', models.CharField(choices=[('pro', 'Pro rename card — 0 ★'), ('stars', 'Stars — burned')], max_length=10)),
                ('cost_stars', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='username_history', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'username history',
                'ordering': ['-created_at'],
            },
        ),
    ]
