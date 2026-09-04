from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Skill(models.Model):
    """A reusable building workflow shared by one builder with others.

    Prompts are knowledge artifacts, not executable instructions. BlaqVibes
    records usage so usefulness can be measured by builders and later tied to
    real projects without turning the product into a prompt dump.
    """
    DIFFICULTY_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    ]
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='skills')
    title = models.CharField(max_length=140)
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    summary = models.CharField(max_length=260)
    problem = models.TextField(max_length=1000)
    workflow = models.TextField(max_length=5000)
    tools = models.CharField(max_length=300, blank=True)
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default='beginner')
    expected_output = models.CharField(max_length=500, blank=True)
    tags = models.CharField(max_length=300, blank=True)
    uses = models.PositiveIntegerField(default=0)
    projects_created = models.PositiveIntegerField(default=0)
    stars = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uses', '-stars', '-created_at']
        indexes = [
            models.Index(fields=['is_published', '-uses']),
            models.Index(fields=['creator', '-created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:150] or 'skill'
            slug = base
            i = 1
            while Skill.objects.filter(slug=slug).exists():
                slug = f'{base}-{i}'
                i += 1
            self.slug = slug
        try:
            from .prompt_sanitize import sanitize_prompt
            self.workflow = sanitize_prompt(self.workflow)[:5000]
            self.problem = sanitize_prompt(self.problem)[:1000]
            self.summary = sanitize_prompt(self.summary)[:260]
            self.expected_output = sanitize_prompt(self.expected_output)[:500]
            self.tools = sanitize_prompt(self.tools)[:300]
            self.tags = sanitize_prompt(self.tags)[:300]
        except Exception:
            pass
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('skill_detail', args=[self.slug])

    def __str__(self):
        return self.title


class SkillUse(models.Model):
    """Intentional use of a skill; optional project proves the result."""
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='applications')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='skill_uses')
    project = models.ForeignKey(
        'gallery.AppProject', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='skill_uses'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['skill', '-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['project']),
        ]

    def __str__(self):
        return f'@{self.user.username} used {self.skill.slug}'


# Attribution bridge: an explicit "Use this skill" action reserves the next
# project the builder creates for that skill. Only a recent unclaimed use can
# attach, so an old click cannot unexpectedly label a later project. This keeps
# the publish flow unchanged while making the skill -> build -> proof loop real.
def _attach_skill_use(sender, instance, created, **kwargs):
    if not created or not getattr(instance, 'owner_id', None):
        return
    from datetime import timedelta
    from django.utils import timezone
    use = (
        SkillUse.objects.filter(
            user_id=instance.owner_id,
            project__isnull=True,
            created_at__gte=timezone.now() - timedelta(hours=2),
        )
        .select_related('skill')
        .order_by('-created_at')
        .first()
    )
    if not use:
        return
    attached = SkillUse.objects.filter(pk=use.pk, project__isnull=True).update(project_id=instance.pk)
    if attached:
        Skill.objects.filter(pk=use.skill_id).update(projects_created=models.F('projects_created') + 1)


from django.db.models.signals import post_save
post_save.connect(_attach_skill_use, sender='gallery.AppProject', dispatch_uid='gallery.attach_skill_use')
