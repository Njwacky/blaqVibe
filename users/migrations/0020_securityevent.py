from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('users', '0019_profile_name_persona')]

    operations = [
        migrations.CreateModel(
            name='SecurityEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event', models.CharField(choices=[('login_first_device', 'First recognised sign-in'), ('login_new_device', 'New device or network sign-in'), ('login_recognized_device', 'Recognised sign-in'), ('password_changed', 'Password changed'), ('sessions_revoked', 'Other sessions revoked'), ('git_tokens_revoked', 'Git credentials revoked')], max_length=32)),
                ('ip_hash', models.CharField(blank=True, max_length=64)),
                ('device_hash', models.CharField(blank=True, max_length=64)),
                ('detail', models.CharField(blank=True, max_length=120)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='security_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(model_name='securityevent', index=models.Index(fields=['user', '-created_at'], name='users_secur_user_id_f93b37_idx')),
    ]
