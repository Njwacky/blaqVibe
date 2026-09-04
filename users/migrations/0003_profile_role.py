
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_profile_stars_balance'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='role',
            field=models.CharField(choices=[('user', 'User'), ('moderator', 'Moderator'), ('admin', 'Admin'), ('superadmin', 'Super Admin')], default='user', help_text='Admin role — backend only, never in JS', max_length=20),
        ),
    ]
