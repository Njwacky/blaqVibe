from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('gallery', '0013_appproject_feed_indexes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Skill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=140)),
                ('slug', models.SlugField(blank=True, max_length=170, unique=True)),
                ('summary', models.CharField(max_length=260)),
                ('problem', models.TextField(max_length=1000)),
                ('workflow', models.TextField(max_length=5000)),
                ('tools', models.CharField(blank=True, max_length=300)),
                ('difficulty', models.CharField(choices=[('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced')], default='beginner', max_length=20)),
                ('expected_output', models.CharField(blank=True, max_length=500)),
                ('tags', models.CharField(blank=True, max_length=300)),
                ('uses', models.PositiveIntegerField(default=0)),
                ('projects_created', models.PositiveIntegerField(default=0)),
                ('stars', models.PositiveIntegerField(default=0)),
                ('is_published', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('creator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='skills', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-uses', '-stars', '-created_at'],
                'indexes': [
                    models.Index(fields=['is_published', '-uses'], name='gallery_skil_is_publ_4d9a1f_idx'),
                    models.Index(fields=['creator', '-created_at'], name='gallery_skil_creator_3d8e7d_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='SkillUse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='skill_uses', to='gallery.appproject')),
                ('skill', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='applications', to='gallery.skill')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='skill_uses', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [
                    models.Index(fields=['skill', '-created_at'], name='gallery_skil_skill_id_5e4e8c_idx'),
                    models.Index(fields=['user', '-created_at'], name='gallery_skil_user_id_8bfc0b_idx'),
                    models.Index(fields=['project'], name='gallery_skil_project_9a8e72_idx'),
                ],
            },
        ),
    ]
