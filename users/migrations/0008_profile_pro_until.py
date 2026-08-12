from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0007_sitesettings_auto_run_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='pro_until',
            field=models.DateTimeField(
                blank=True,
                help_text='When a Pro trial/prize expires. Null + is_pro means permanent (admin).',
                null=True,
            ),
        ),
    ]
