from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_profile_git_token_hash'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='canvas_url',
            field=models.URLField(blank=True, help_text='Public canvas/portfolio board URL (Koboyo, Figma, Miro, etc.)'),
        ),
    ]
