from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

class Profile(models.Model):
    ROLE_CHOICES = [('user','User'),('moderator','Moderator'),('admin','Admin'),('superadmin','Super Admin')]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    bio = models.CharField(max_length=280, blank=True, help_text="280 chars like Twitter — what you build")
    location = models.CharField(max_length=80, blank=True)
    website = models.URLField(blank=True)
    github = models.CharField(max_length=80, blank=True, help_text="github username without @")
    twitter = models.CharField(max_length=80, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    stars_balance = models.PositiveIntegerField(default=5, help_text="Stars to trade — earn by publishing, spend to download")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user', help_text="Admin role — backend only, never in JS")
    is_pro = models.BooleanField(default=False, help_text="Pro plan — can see who viewed your vibes, AI README, money")
    email_verified = models.BooleanField(default=False)
    pro_since = models.DateTimeField(null=True, blank=True)
    pro_until = models.DateTimeField(null=True, blank=True, help_text="When a Pro trial/prize expires. Null + is_pro means permanent (admin).")
    auto_language = models.BooleanField(default=True, help_text="Auto detect language % from ZIP")
    nolo_enabled = models.BooleanField(default=True, help_text="Nolo auto-review on upload")
    auto_thumbnail = models.BooleanField(default=True)
    allow_trading = models.BooleanField(default=True, help_text="If off, your vibes are free (0 ★)")
    email_on_trade = models.BooleanField(default=True)
    email_on_review = models.BooleanField(default=True)
    show_language = models.BooleanField(default=True)
    allow_forks = models.BooleanField(default=True)
    allow_prs = models.BooleanField(default=True)
    allow_comments = models.BooleanField(default=True)
    allow_reviews = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(stars_balance__gte=0), name='stars_balance_gte_0')
        ]
    def __str__(self): return f"@{self.user.username} ({self.role})"
    def is_moderator(self): return self.role in ('moderator','admin','superadmin')
    def is_admin(self): return self.role in ('admin','superadmin')
    def is_superadmin(self): return self.role == 'superadmin'

    @property
    def is_pro_active(self):
        if not self.is_pro:
            return False
        if self.pro_until and timezone.now() > self.pro_until:
            return False
        return True

    def followers_count(self): return self.followers.count()
    def following_count(self): return self.user.following.count()
    def vibes_count(self): return self.user.projects.filter(status='published').count()
    def stars_received(self):
        from django.db.models import Sum
        return self.user.projects.aggregate(s=Sum('stars'))['s'] or 0
    def rank(self):
        from gallery.ranks import contributor_bonus
        return contributor_bonus(self.user)

class SiteSettings(models.Model):
    """Global toggles — superadmin only, backend only"""
    maintenance = models.BooleanField(default=False)
    clamav_enabled = models.BooleanField(default=True)
    r2_enabled = models.BooleanField(default=True)
    search_enabled = models.BooleanField(default=True)
    pwa_enabled = models.BooleanField(default=True)
    auto_run_enabled = models.BooleanField(default=False, help_text="If On, every full app auto-runs on upload to live URL; if Off, manual Run button only")
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

class Follow(models.Model):
    follower = models.ForeignKey(User, on_delete=models.CASCADE, related_name='following')
    following = models.ForeignKey(User, on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('follower','following')
        indexes = [models.Index(fields=['follower']), models.Index(fields=['following'])]
    def __str__(self): return f"{self.follower} → {self.following}"

class AdminLog(models.Model):
    """Audit log — who did what, when. Backend only, never in JS."""
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='admin_logs')
    action = models.CharField(max_length=50)
    target = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']

@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
