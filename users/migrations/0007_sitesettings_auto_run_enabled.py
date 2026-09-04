
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_profile_is_pro_profile_pro_since'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='auto_run_enabled',
            field=models.BooleanField(default=False, help_text='If On, every full app auto-runs on upload to live URL; if Off, manual Run button only'),
        ),
    ]
