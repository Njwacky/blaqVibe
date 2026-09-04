
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0015_alter_starevent_reason_payout'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='git_token_hash',
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
