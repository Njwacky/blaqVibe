
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0020_securityevent'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='notify_on_comment',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='notify_on_follow',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='notify_on_fork',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='notify_on_milestone',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='notify_on_star',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='notify_on_trade',
            field=models.BooleanField(default=True),
        ),
        migrations.CreateModel(
            name='Achievement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.CharField(max_length=40)),
                ('earned_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='achievements', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-earned_at'],
                'unique_together': {('user', 'slug')},
            },
        ),
        migrations.CreateModel(
            name='XPEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.PositiveSmallIntegerField()),
                ('reason', models.CharField(choices=[('publish', 'Published a vibe'), ('star_received', 'Received a star'), ('fork_received', 'Received a fork/remix'), ('comment_given', 'Gave feedback'), ('review_given', 'Wrote a review'), ('trade_made', 'Traded for a vibe'), ('trade_received', 'Someone traded your vibe'), ('pr_merged', 'Pull request merged'), ('verified', 'Vibe passed the trust scan'), ('challenge_win', 'Won a challenge')], max_length=20)),
                ('ref', models.CharField(blank=True, default='', max_length=120)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='xp_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['user', '-created_at'], name='users_xpeve_user_id_7a61dc_idx')],
                'unique_together': {('user', 'reason', 'ref')},
            },
        ),
    ]
