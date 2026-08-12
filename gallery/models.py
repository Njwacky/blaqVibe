from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.text import slugify

class Category(models.Model):
    TYPE_CHOICES = [('snippet','Snippet'), ('full_app','Full App')]
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='snippet')
    order = models.PositiveIntegerField(default=0)
    def __str__(self): return self.name

class Tag(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(unique=True)
    def __str__(self): return self.name

class AppProject(models.Model):
    STATUS_CHOICES = [('pending','Pending Scan/Review'),('published','Published'),('quarantined','Quarantined — Virus/Secret')]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='projects')
    tags = models.ManyToManyField(Tag, blank=True)
    short_description = models.CharField(max_length=260)
    readme = models.TextField(help_text="Markdown — required, min 100 chars")
    readme_html = models.TextField(blank=True)
    html_code = models.TextField(blank=True)
    css_code = models.TextField(blank=True)
    js_code = models.TextField(blank=True)
    zip_file = models.FileField(upload_to='apps/zips/', blank=True, null=True)
    tech_stack = models.CharField(max_length=200, blank=True)
    ai_generated = models.BooleanField(default=False)
    ai_tool = models.CharField(max_length=50, blank=True)
    ai_prompt = models.TextField(blank=True)
    thumbnail = models.ImageField(upload_to='thumbnails/', blank=True, null=True)
    file_tree = models.JSONField(default=dict, blank=True)
    file_count = models.PositiveIntegerField(default=0)
    language_stats = models.JSONField(default=dict, blank=True)  # {'Python':68,'JavaScript':22}
    star_cost = models.PositiveIntegerField(default=0, help_text="Stars to trade to download (0=free, 1=Bronze, 3=Silver, 5=Gold)")
    price_zar = models.PositiveIntegerField(default=0, help_text="Money price in ZAR (0=free, 50=R50) — for real money via Paystack")
    ai_readme = models.TextField(blank=True, help_text="AI-generated README, backend only")
    forked_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='forks')
    scan_report = models.JSONField(default=dict, blank=True)  # backend only, never sent raw to JS
    avg_rating = models.FloatField(default=0)  # cached from Reviews
    review_count = models.PositiveIntegerField(default=0)
    views = models.PositiveIntegerField(default=0)
    clones = models.PositiveIntegerField(default=0)
    copies = models.PositiveIntegerField(default=0)
    stars = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['status']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['status', '-stars']),
        ]
    def save(self, *args, **kwargs):
        try:
            if not self.slug:
                base = slugify(self.title)[:200]
                slug = base
                i=1
                while AppProject.objects.filter(slug=slug).exists():
                    slug = f"{base}-{i}"; i+=1
                self.slug = slug
            if self.readme:
                try:
                    from .sanitizers import render_readme
                    self.readme_html = render_readme(self.readme)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('readme render failed')
            # html_code is stored raw and only rendered inside a sandboxed iframe.
            # Bleach-on-save guts real snippet dashboards.
            # Sanitize ai_prompt — many prompt fields, must be checked
            if self.ai_prompt:
                try:
                    from .prompt_sanitize import sanitize_prompt
                    self.ai_prompt = sanitize_prompt(self.ai_prompt)
                except Exception:
                    pass
            # Also sanitize tech_stack and short_description for prompt-like injection
            if self.tech_stack:
                try:
                    import bleach
                    self.tech_stack = bleach.clean(self.tech_stack, tags=[], strip=True)[:200]
                except: pass
            if self.short_description:
                try:
                    import bleach
                    self.short_description = bleach.clean(self.short_description, tags=[], strip=True)[:260]
                except: pass
            # Auto language detect (crush silently)
            if self.zip_file and not self.language_stats:
                try:
                    from .language import detect_languages
                    if hasattr(self.zip_file, 'path') and self.zip_file.path:
                        self.language_stats = detect_languages(self.zip_file.path)
                except: pass
        except Exception:
            import logging
            logging.getLogger(__name__).exception('AppProject.save pre-process failed')
        super().save(*args, **kwargs)
    def get_absolute_url(self):
        return reverse('app_detail', args=[self.slug])
    def __str__(self): return self.title
    def rank_bonus(self):
        from .ranks import contributor_bonus
        return contributor_bonus(self.owner)['bonus']

class AppFile(models.Model):
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='files')
    path = models.CharField(max_length=500)
    size = models.PositiveIntegerField(default=0)
    class Meta:
        ordering = ['path']
    def __str__(self): return self.path

class Star(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='star_set')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('user','project')

class Comment(models.Model):
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='replies')
    body = models.TextField(max_length=2000)
    body_html = models.TextField(blank=True)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['created_at']
    def save(self, *args, **kwargs):
        from .sanitizers import render_markdown_inline
        self.body_html = render_markdown_inline(self.body)
        super().save(*args, **kwargs)

class ScanJob(models.Model):
    """Queue tracking — backend only, JS polls status via scan_status view (no secrets)."""
    project = models.OneToOneField(AppProject, on_delete=models.CASCADE, related_name='scan_job')
    task_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, default='queued', choices=[('queued','Queued'),('scanning','Scanning'),('clean','Clean'),('quarantined','Quarantined'),('failed','Failed')])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class AppReport(models.Model):
    REASON_CHOICES = [('spam','Spam'),('malware','Malware/Virus'),('copyright','Copyright'),('other','Other')]
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='reports')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='other')
    details = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return f"{self.project.slug} — {self.reason}"

class AppVersion(models.Model):
    """Git-like versions — Why not overwrite zip? History + stars preserved, rollback."""
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='versions')
    version = models.CharField(max_length=20, default='1.0.0')
    zip_file = models.FileField(upload_to='apps/versions/')
    changelog = models.CharField(max_length=280, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
    def __str__(self): return f"{self.project.slug} v{self.version}"

class Trade(models.Model):
    """Star trading — backend only, no JS secrets."""
    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trades_bought')
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trades_sold')
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='trades')
    cost = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('buyer','project')  # one trade per buyer per app
    def __str__(self): return f"{self.buyer} → {self.project.slug} ({self.cost}★)"

class VibeView(models.Model):
    """Who viewed your vibe — Pro only sees names. Backend only."""
    viewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='vibe_views')
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='viewer_logs')
    count = models.PositiveIntegerField(default=1)
    last_viewed = models.DateTimeField(auto_now=True)
    first_viewed = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('viewer','project')
        indexes = [models.Index(fields=['project','-last_viewed'])]
    def __str__(self): return f"{self.viewer} → {self.project.slug} x{self.count}"

class Sale(models.Model):
    """Real money via Paystack — backend webhook verifies, no JS secrets."""
    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sales_bought')
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sales_sold')
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='sales')
    amount_zar = models.PositiveIntegerField()
    paystack_ref = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('buyer','project')
    def __str__(self): return f"{self.buyer} → {self.project.slug} R{self.amount_zar}"

class Review(models.Model):
    """Human review 1-5 ★ + text, separate from Comment/Star. One per user per vibe."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(choices=[(1,'1'),(2,'2'),(3,'3'),(4,'4'),(5,'5')])
    text = models.TextField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ('user','project')
        ordering = ['-created_at']
    def save(self, *args, **kwargs):
        try:
            from .prompt_sanitize import sanitize_prompt
            self.text = sanitize_prompt(self.text)[:1000]
        except: pass
        super().save(*args, **kwargs)
        try:
            from django.db.models import Avg, Count
            agg = Review.objects.filter(project=self.project).aggregate(avg=Avg('rating'), cnt=Count('id'))
            self.project.avg_rating = round(agg['avg'] or 0, 1)
            self.project.review_count = agg['cnt'] or 0
            self.project.save(update_fields=['avg_rating','review_count'])
        except: pass
    def __str__(self): return f"{self.user} → {self.project.slug} {self.rating}★"

class VibeBattle(models.Model):
    vibe_a = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='battles_as_a')
    vibe_b = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='battles_as_b')
    votes_a = models.PositiveIntegerField(default=0)
    votes_b = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
    def __str__(self): return f"Battle {self.id}: {self.vibe_a.slug} vs {self.vibe_b.slug} ({self.votes_a}-{self.votes_b})"

class BattleVote(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='battle_votes')
    battle = models.ForeignKey(VibeBattle, on_delete=models.CASCADE, related_name='votes')
    choice = models.CharField(max_length=1, choices=[('a','A'),('b','B')])
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('user','battle')
    def __str__(self): return f"{self.user} voted {self.choice} on {self.battle_id}"

class Deploy(models.Model):
    STATUS_CHOICES = [('running','Running'),('expired','Expired'),('failed','Failed')]
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='deploys')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='deploys')
    token = models.CharField(max_length=40, unique=True)
    live_url = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='running')
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return f"{self.token} → {self.project.slug} ({self.status})"

class Season(models.Model):
    number = models.PositiveIntegerField(unique=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    winner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return f"Season {self.number} ({self.start.date()} → {self.end.date()})"

class Challenge(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    bounty_stars = models.PositiveIntegerField(default=10, help_text="Stars bounty for winner")
    tag = models.SlugField(unique=True, help_text="e.g., challenge-week-12")
    start = models.DateTimeField()
    end = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    winner = models.ForeignKey(AppProject, null=True, blank=True, on_delete=models.SET_NULL, related_name='won_challenges')
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-start']
    def __str__(self): return f"{self.title} ({self.tag})"

class PullRequest(models.Model):
    STATUS_CHOICES = [('open','Open'),('merged','Merged'),('closed','Closed')]
    source = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='prs_outgoing')
    target = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='prs_incoming')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='prs')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=2000)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['target','status']), models.Index(fields=['source'])]
    def __str__(self): return f"PR #{self.id} {self.source.slug} → {self.target.slug} ({self.status})"


class Notification(models.Model):
    KIND_CHOICES = [
        ('comment', 'Comment'),
        ('follow', 'Follow'),
        ('trade', 'Trade'),
        ('sale', 'Sale'),
        ('pr', 'Pull request'),
        ('published', 'Published'),
        ('quarantined', 'Quarantined'),
        ('review', 'Review'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=400, blank=True)
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'is_read', '-created_at'])]

    def __str__(self):
        return f'{self.user} {self.kind}: {self.title}'


class Bookmark(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookmarks')
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='bookmarks')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'project')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} ♥ {self.project.slug}'
