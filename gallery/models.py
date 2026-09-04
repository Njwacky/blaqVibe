from django.core.validators import MaxValueValidator
from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.text import slugify

# Price ceilings — see the on AppProject.star_cost.
MAX_STAR_COST = 5
MAX_PRICE_ZAR = 9999

from .taxonomy import (
    DEFAULT_KIND,
    KIND_CHOICES,
    PREVIEW_MODES,
    UPLOAD_KIND_CHOICES,
    kind_icon,
    kind_label,
    kind_meta,
)
from .trust import (
    TRUST_CHOICES,
    TRUST_UNKNOWN,
    trust_meta as trust_meta_table,
)

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
    # 'removed' is the soft-delete state: gone from feed/search/detail for
    # strangers, but buyers who paid (Trade/Sale) keep their download. It's a
    # status rather than a boolean because the status field already gates every
    # list and view — one more state rides every existing filter.
    STATUS_CHOICES = [
        ('pending','Pending Scan/Review'),
        ('published','Published'),
        ('quarantined','Quarantined — Virus/Secret'),
        ('removed','Removed — buyers keep access'),
    ]
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
    star_cost = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(MAX_STAR_COST)],
        help_text=f"Stars to trade to download (0=free … {MAX_STAR_COST}=Gold)",
    )
    price_zar = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(MAX_PRICE_ZAR)],
        help_text=f"Money price in ZAR (0=free, 50=R50) — max R{MAX_PRICE_ZAR}",
    )
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
    kind = models.CharField(
        max_length=20, choices=KIND_CHOICES, default=DEFAULT_KIND, db_index=True,
        help_text='What sort of program this is — auto-detected, creator can override.',
    )
    creator_kind = models.CharField(
        max_length=20, choices=UPLOAD_KIND_CHOICES, blank=True, default='',
        help_text="Creator's own pick. Blank = trust auto-detection.",
    )
    kind_source = models.CharField(
        max_length=20, blank=True, default='',
        help_text='heuristic | claude | gemini | groq | creator | moderator',
    )
    kind_confidence = models.FloatField(default=0)
    kind_evidence = models.JSONField(default=list, blank=True)
    # Honest capability, computed at classify time — never a guess in a template.
    preview_mode = models.CharField(
        max_length=12, choices=PREVIEW_MODES, default='files',
        help_text=('snippet = inline HTML runs in the sandboxed iframe. '
                   'static_zip = a static site in the ZIP runs there too. '
                   'files = file list + README only.'),
    )
    # Which document inside a static_zip is the entry to assemble+run. Stored
    # for the same reason preview_mode is: the runner view must not re-scan the
    # archive on every hit, and a wrong entry is correctable by a rescan.
    static_entry = models.CharField(max_length=500, blank=True, default='')
    # Trust badge
    # Public verdict derived from scan evidence by gallery.trust. Stored for
    # the same 4-point reasons `kind` is stored: (1) the feed must filter and
    # sort on it in SQL; (2) deriving per card would re-run regex scans on
    # every page view; (3) a stored verdict is auditable — trust_graded_at
    # says when it was decided; (4) it is correctable — the pipeline rewrites
    # it on every rescan and any content change resets it to unknown.
    # WRITER RULE: only gallery.trust.apply_trust_grade (pipeline) and
    # invalidate_trust (content change) may write this field. Never a form,
    # never the API, never a template — that is what makes it unfakeable.
    trust = models.CharField(
        max_length=10, choices=TRUST_CHOICES, default=TRUST_UNKNOWN, db_index=True,
        help_text='verified | scanned | unknown — pipeline-written only, see gallery.trust.',
    )
    trust_graded_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the trust tier was last written (grade or reset).',
    )
    @property
    def trust_meta(self):
        """Fixed presentation row for the badge — server table, never
        user-supplied, so a creator cannot style or spoof a ✓."""
        return trust_meta_table(getattr(self, 'trust', TRUST_UNKNOWN) or TRUST_UNKNOWN)

    @property
    def trust_reasons(self):
        """Safe per-check sentences for the detail page ("read"): fixed
        strings only — no filenames, no secret values, nothing user-typed."""
        from .trust import trust_reasons as _trust_reasons
        return _trust_reasons(self)
    # Global "how interesting is this" score, 0-100. Recomputed by a task,
    # never inside a request. See gallery.interest.
    appeal_score = models.FloatField(default=0, db_index=True)
    appeal_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['status']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['status', '-stars']),
            # Feed ordering is always "published rows, best first" — a
            # composite index keeps that a range scan instead of a sort of
            # the whole table once there are tens of thousands of vibes.
            models.Index(fields=['status', '-appeal_score'], name='gallery_app_status_appeal_idx'),
            models.Index(fields=['status', 'kind', '-appeal_score'], name='gallery_app_kind_appeal_idx'),
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
                except Exception: pass
            if self.short_description:
                try:
                    import bleach
                    self.short_description = bleach.clean(self.short_description, tags=[], strip=True)[:260]
                except Exception: pass
            # Auto language detect (crush silently). Reads through the
            # storage API so it works on local disk AND S3/R2 — FieldFile.path
            # raises NotImplementedError on remote backends.
            # Check the toggle here (not in the upload form) because save() is
            # the single entry point for every path that creates or updates a
            # project (publish, edit, fork, git push) — gating here makes every
            # path respect the choice. It defaults True because auto-detect is
            # the primary discovery signal; manual tech_stack is a power-user
            # opt-out.
            if self.zip_file and not self.language_stats:
                try:
                    if getattr(self.owner.profile, 'auto_language', True):
                        from .language import detect_languages_from_field
                        self.language_stats = detect_languages_from_field(self.zip_file)
                except Exception: pass
        except Exception:
            import logging
            logging.getLogger(__name__).exception('AppProject.save pre-process failed')
        super().save(*args, **kwargs)
    def get_absolute_url(self):
        return reverse('app_detail', args=[self.slug])
    def __str__(self): return self.title

    # Kind helpers (templates + API read these, never a raw string)
    @property
    def kind_meta(self):
        return kind_meta(self.kind)

    @property
    def kind_label(self):
        return kind_label(self.kind)

    @property
    def kind_icon(self):
        return kind_icon(self.kind)

    @property
    def can_run_preview(self):
        """True only when there is really something to run in the iframe.

        Two honest paths: an inline snippet (html_code) or a static site in
        the ZIP whose entry we already found (static_entry). Anything else
        renders the plain "no live preview" state — never faked chrome.
        """
        if self.preview_mode == 'snippet':
            return bool((self.html_code or '').strip())
        if self.preview_mode == 'static_zip':
            return bool(self.zip_file) and bool((self.static_entry or '').strip())
        return False

    @property
    def preview_note(self):
        """One honest sentence about what a visitor can do here."""
        if self.can_run_preview:
            if self.preview_mode == 'static_zip':
                return 'This static site runs live in a sandboxed preview.'
            return 'Runs live in a sandboxed preview.'
        if self.zip_file:
            return 'No live preview — browse the file list and README, or download the ZIP.'
        return 'No live preview available.'

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
        from .profanity import contains_profanity
        # Defense in depth: a shell/admin write that skipped the form must
        # still never render the words. The raw body stays for moderators.
        if contains_profanity(self.body):
            self.is_hidden = True
            self.body_html = (
                '<p>This comment was hidden because it used language '
                'that is not allowed here.</p>'
            )
        else:
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
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('resolved', 'Resolved'),
        ('ignored', 'Ignored'),
    ]
    OUTCOME_CHOICES = [
        ('', '—'),
        ('no_action', 'Dismissed (no violation found)'),
        ('quarantined', 'Vibe quarantined'),
        ('removed', 'Vibe removed (soft delete — buyers keep downloads)'),
        ('deleted', 'Vibe deleted'),
    ]
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='reports')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='other')
    details = models.CharField(max_length=500, blank=True)
    # Report lifecycle. Before this existed a report row was just a row:
    # moderator opened /admin/dashboard/, saw it, and the closing action
    # (if any) happened in the moderator's head. Status + outcome make the
    # triage explicit and auditable end-to-end.
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    outcome = models.CharField(max_length=12, choices=OUTCOME_CHOICES, default='', blank=True)
    handled_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='handled_reports',
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self): return f"{self.project.slug} — {self.reason} ({self.status})"

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

class ProjectCoOwner(models.Model):
    """A revenue share in a vibe's star trades.
        The OWNER keeps whatever the co-owners don't: owner_share =
        100 − Σ(co-owner share_percent). Adding or removing a co-owner never
        rewrites existing rows — the remainder always rebalances itself.
    """
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='co_owners')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='co_owned_projects')
    share_percent = models.PositiveSmallIntegerField(help_text='% of star trade revenue (1–100). Owner keeps the remainder.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('project', 'user')
        constraints = [
            models.CheckConstraint(
                check=models.Q(share_percent__gte=1) & models.Q(share_percent__lte=100),
                name='co_owner_share_between_1_and_100',
            ),
        ]

    def __str__(self):
        return f'@{self.user.username} {self.share_percent}% of {self.project.slug}'

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
    """Star trading — a money record.
    """
    buyer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='trades_bought')
    seller = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='trades_sold')
    project = models.ForeignKey(AppProject, on_delete=models.PROTECT, related_name='trades')
    cost = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=['buyer', 'project'])]
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
    """Real money via Paystack — backend webhook verifies, no JS secrets.

    Same FK rules as Trade and for the same reason: ZAR receipts are money
    records. PROTECT the project (buyers keep the ZIP), SET_NULL the people
    (account deletion never erases the counterparty's receipt).
    """
    buyer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='sales_bought')
    seller = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='sales_sold')
    project = models.ForeignKey(AppProject, on_delete=models.PROTECT, related_name='sales')
    amount_zar = models.PositiveIntegerField()
    paystack_ref = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('buyer','project')
        constraints = [
            models.UniqueConstraint(
                fields=['paystack_ref'],
                name='sale_unique_paystack_ref',
                condition=~models.Q(paystack_ref=''),
            ),
        ]
    def __str__(self): return f"{self.buyer} → {self.project.slug} R{self.amount_zar}"

class PaymentIntent(models.Model):
    """Frozen checkout — webhook fulfills this row, not the live price_zar."""
    STATUS_CHOICES = [('pending', 'Pending'), ('paid', 'Paid'), ('failed', 'Failed')]
    reference = models.CharField(max_length=100, unique=True)
    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payment_intents')
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='payment_intents')
    amount_zar = models.PositiveIntegerField()
    amount_kobo = models.PositiveIntegerField()
    currency = models.CharField(max_length=8, default='ZAR')
    authorization_url = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['reference']),
            models.Index(fields=['buyer', 'project', 'status']),
        ]

    def __str__(self):
        return f'{self.reference} {self.status} R{self.amount_zar}'

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
            from .profanity import contains_profanity
            self.text = sanitize_prompt(self.text)[:1000]
            # Rating can stay; the words cannot. The form already rejects
            # the POST — this is the ORM/admin backstop.
            if contains_profanity(self.text):
                self.text = ''
        except Exception: pass
        super().save(*args, **kwargs)
        try:
            from django.db.models import Avg, Count
            agg = Review.objects.filter(project=self.project).aggregate(avg=Avg('rating'), cnt=Count('id'))
            self.project.avg_rating = round(agg['avg'] or 0, 1)
            self.project.review_count = agg['cnt'] or 0
            self.project.save(update_fields=['avg_rating','review_count'])
        except Exception: pass

    def delete(self, *args, **kwargs):
        project = self.project
        super().delete(*args, **kwargs)
        try:
            from django.db.models import Avg, Count
            agg = Review.objects.filter(project=project).aggregate(avg=Avg('rating'), cnt=Count('id'))
            project.avg_rating = round(agg['avg'] or 0, 1)
            project.review_count = agg['cnt'] or 0
            project.save(update_fields=['avg_rating','review_count'])
        except Exception: pass

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

# Deploy model removed. delete instead of keep? It promised
# "Running" live deployments that never existed — the view only redirected
# to the in-app preview. Dead capability code is a lie in the schema; when
# real hosting ships it gets a real model designed for it.

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
        ('tip', 'Tip'),
        ('co_owner', 'Co-owner'),
        ('trade', 'Trade'),
        ('sale', 'Sale'),
        ('pr', 'Pull request'),
        ('published', 'Published'),
        ('quarantined', 'Quarantined'),
        ('review', 'Review'),
        ('challenge', 'Challenge'),
        ('payout', 'Payout'),
        ('git_push', 'Git push'),
        ('report', 'Report'),
        # Added with the retention work: the social half of the loop.
        ('star', 'Star'),
        ('fork', 'Fork'),
        ('milestone', 'Milestone'),
        ('achievement', 'Achievement'),
        ('git_push_rejected', 'Git push rejected'),
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

class KindAffinity(models.Model):
    """What one user has shown they like, per program kind.
        One row per (user, kind) — at most 14 rows per user, forever.
    """

    # Weights per interaction. Ordered by how much intent each one proves:
    # downloading (or paying for) something is a far stronger statement than
    # scrolling past its card.
    EVENT_WEIGHTS = {
        'view': 1.0,
        'preview': 2.0,
        'star': 3.0,
        'save': 3.0,
        'comment': 3.0,
        'fork': 5.0,
        'download': 5.0,
        'trade': 8.0,
        'publish': 6.0,   # what you build is what you are into
        'pick': 10.0,     # explicit "I like games" from onboarding
    }
    # Score halves after this many days without reinforcement.
    HALF_LIFE_DAYS = 30.0

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='kind_affinities')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    score = models.FloatField(default=0)
    events = models.PositiveIntegerField(default=0)
    last_event = models.CharField(max_length=20, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'kind')
        ordering = ['-score']
        indexes = [models.Index(fields=['user', '-score'])]

    def __str__(self):
        return f'{self.user} likes {self.kind} ({self.score:.1f})'

class CloneEvent(models.Model):
    """Append-only clone log — the admin charts' source of truth.
    """
    SOURCE_CHOICES = [('git', 'git clone/fetch'), ('zip', 'zip download')]
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='clone_events')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='clone_events')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='zip')
    ip_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=['project', '-created_at'])]

    def __str__(self):
        who = f'@{self.user.username}' if self.user else 'anon'
        return f'{who} cloned {self.project.slug} via {self.source}'
