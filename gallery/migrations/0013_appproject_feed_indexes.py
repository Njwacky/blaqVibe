from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0012_challenge'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='appproject',
            index=models.Index(fields=['status', '-created_at'], name='gallery_app_status_created_idx'),
        ),
        migrations.AddIndex(
            model_name='appproject',
            index=models.Index(fields=['status', '-stars'], name='gallery_app_status_stars_idx'),
        ),
    ]
