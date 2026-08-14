from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0016_paymentintent'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentintent',
            name='authorization_url',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='paymentintent',
            name='currency',
            field=models.CharField(default='ZAR', max_length=8),
        ),
        migrations.AddField(
            model_name='paymentintent',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='sale',
            constraint=models.UniqueConstraint(
                condition=~models.Q(paystack_ref=''),
                fields=('paystack_ref',),
                name='sale_unique_paystack_ref',
            ),
        ),
    ]
