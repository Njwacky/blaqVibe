
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0014_notification_bookmark'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='appproject',
            new_name='gallery_app_status_f64dad_idx',
            old_name='gallery_app_status_created_idx',
        ),
        migrations.RenameIndex(
            model_name='appproject',
            new_name='gallery_app_status_e042c5_idx',
            old_name='gallery_app_status_stars_idx',
        ),
        migrations.RenameIndex(
            model_name='notification',
            new_name='gallery_not_user_id_8220dc_idx',
            old_name='gallery_not_user_id_read_idx',
        ),
    ]
