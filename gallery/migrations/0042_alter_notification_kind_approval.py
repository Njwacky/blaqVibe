# Generated for admin approval notifications — Brevo email + in-app
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0041_appproject_build_choice'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(choices=[('comment', 'Comment'), ('follow', 'Follow'), ('tip', 'Tip'), ('co_owner', 'Co-owner'), ('trade', 'Trade'), ('sale', 'Sale'), ('pr', 'Pull request'), ('published', 'Published'), ('quarantined', 'Quarantined'), ('review', 'Review'), ('challenge', 'Challenge'), ('payout', 'Payout'), ('git_push', 'Git push'), ('report', 'Report'), ('upload', 'ZIP upload'), ('star', 'Star'), ('fork', 'Fork'), ('milestone', 'Milestone'), ('achievement', 'Achievement'), ('git_push_rejected', 'Git push rejected'), ('approval', 'Approval needed'), ('pending', 'Pending approval'), ('review_needed', 'Review needed'), ('challenge_draft', 'Challenge draft')], max_length=20),
        ),
    ]
