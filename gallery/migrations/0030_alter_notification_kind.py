
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0029_alter_appproject_price_zar_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(choices=[('comment', 'Comment'), ('follow', 'Follow'), ('tip', 'Tip'), ('co_owner', 'Co-owner'), ('trade', 'Trade'), ('sale', 'Sale'), ('pr', 'Pull request'), ('published', 'Published'), ('quarantined', 'Quarantined'), ('review', 'Review'), ('challenge', 'Challenge'), ('payout', 'Payout'), ('git_push', 'Git push'), ('report', 'Report'), ('star', 'Star'), ('fork', 'Fork'), ('milestone', 'Milestone'), ('achievement', 'Achievement'), ('git_push_rejected', 'Git push rejected')], max_length=20),
        ),
    ]
