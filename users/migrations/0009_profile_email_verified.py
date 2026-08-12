from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_profile_pro_until'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='email_verified',
            field=models.BooleanField(default=False),
        ),
    ]
