
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0021_alter_notification_kind_projectcoowner'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='trade',
            unique_together=set(),
        ),
        migrations.AddIndex(
            model_name='trade',
            index=models.Index(fields=['buyer', 'project'], name='gallery_tra_buyer_i_14ff7f_idx'),
        ),
    ]
