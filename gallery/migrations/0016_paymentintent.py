from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('gallery', '0015_rename_gallery_app_status_created_idx_gallery_app_status_f64dad_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentIntent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference', models.CharField(max_length=100, unique=True)),
                ('amount_zar', models.PositiveIntegerField()),
                ('amount_kobo', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('paid', 'Paid'), ('failed', 'Failed')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('buyer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payment_intents', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payment_intents', to='gallery.appproject')),
            ],
        ),
        migrations.AddIndex(
            model_name='paymentintent',
            index=models.Index(fields=['reference'], name='gallery_pay_referen_idx'),
        ),
        migrations.AddIndex(
            model_name='paymentintent',
            index=models.Index(fields=['buyer', 'project'], name='gallery_pay_buyer_p_idx'),
        ),
    ]
