from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.text import slugify

from .taxonomy import (
    DEFAULT_KIND,
    KIND_CHOICES,
    PREVIEW_MODES,
    UPLOAD_KIND_CHOICES,
    kind_icon,
    kind_label,
    kind_meta,
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
    # strangers, but buyers who paid (Trade/Sale) keep their download.
    # 5 Whys: Why a status, not a boolean? The status field already gates
    # every list and view — one more state rides every existing filter.
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
    # --- What kind of program is this? -----------------------------------
    # 5 Whys: Why store the kind instead of deriving it per request?
    # 1. Discovery has to FILTER and SORT on it; a Python-derived value
    #    cannot be a WHERE clause or an ORDER BY.
    # 2. Deriving it means re-reading the file list for every card on every
    #    page view — 12 extra queries per feed page, forever.
    # 3. The classifier may call an LLM. Deriving on read would put a paid
    #    network call inside a page render.
    # 4. A stored value is auditable: kind_source/kind_evidence say who
    #    decided and why, so a wrong badge can be argued with.
    # 5. It is also correctable — a creator edit or a moderator override
    #    writes the field, and everything downstream follows immediately.
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
        max_length=10, choices=PREVIEW_MODES, default='files',
        help_text='snippet = runs in the sandboxed iframe. files = file list + README only.',
    )
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
    # Public-text fields a staff admin edit or a shell create can write
    # without ever touching AppUploadForm. One list so save(), clean()
    # and the migration all gate the same fields.
    PUBLIC_TEXT_FIELDS = ('title', 'readme', 'short_description', 'tech_stack')

    def dirty_public_fields(self):
        """Names of public-text fields that currently hold blocked words."""
        from .profanity import contains_profanity
        return [
            name for name in self.PUBLIC_TEXT_FIELDS
            if contains_profanity(getattr(self, name, '') or '')
        ]

    def clean(self):
        """Honest errors for admin/form paths that run full_clean().

        Django admin validates through clean() before saving, so a staff
        edit that types a slur into the title gets a form error — not a
        silent rewrite, not a published slur.
        """
        from django.core.exceptions import ValidationError
        from .profanity import PUBLIC_LANGUAGE_ERROR
        errors = {
            name: PUBLIC_LANGUAGE_ERROR for name in self.dirty_public_fields()
        }
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Public-language backstop — runs BEFORE slug generation so a
        # blocked title can never mint a blocked URL either.
        dirty_fields = self.dirty_public_fields()
        try:
            if not self.slug:
                # A dirty title must not seed the slug; the moderation
                # queue still shows the raw title to staff.
                base = slugify('vibe' if dirty_fields else self.title)[:200] or 'vibe'
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
            if self.zip_file and not self.language_stats:
                try:
                    from .language import detect_languages_from_field
                    self.language_stats = detect_languages_from_field(self.zip_file)
                except Exception: pass
        except Exception:
            import logging
            logging.getLogger(__name__).exception('AppProject.save pre-process failed')

        # --- Public-language gate (the ORM/admin backstop) ----------------
        # 5 Whys:
        # 1. Why here? AppUploadForm gates the publish/edit views, but a
        #    staff edit in Django admin or a shell create bypasses every
        #    form and lands straight on the feed and the public API.
        # 2. Why demote to pending instead of raising? A raise would crash
        #    background tasks that re-save an already-dirty legacy row
        #    (appeal scores, classification). Demoting is fail-closed AND
        #    keeps the pipeline alive.
        # 3. Why not rewrite the words? Silent rewriting hides from the
        #    owner what was stored. The raw text stays for moderators in
        #    the queue; it just cannot be public.
        # 4. Why log into scan_report? Moderators opening the queue see
        #    WHY the vibe is held — the reason travels with the row.
        # 5. Why touch update_fields? save(update_fields=['appeal_score'])
        #    must still persist the demotion, or the row stays published.
        if dirty_fields:
            from django.utils import timezone
            if self.status == 'published':
                self.status = 'pending'
            report = dict(self.scan_report or {})
            report['language_gate'] = {
                'fields': dirty_fields,
                'at': timezone.now().isoformat(),
                'note': 'Blocked language in public text — held from the feed until reworded.',
            }
            self.scan_report = report
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                update_fields = list(update_fields)
                for field in ('status', 'scan_report'):
                    if field not in update_fields:
                        update_fields.append(field)
                kwargs['update_fields'] = update_fields

        super().save(*args, **kwargs)
    def get_absolute_url(self):
        return reverse('app_detail', args=[self.slug])
    def __str__(self): return self.title

    # --- Kind helpers (templates + API read these, never a raw string) ---
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
        """True only when there is really something to run in the iframe."""
        return self.preview_mode == 'snippet' and bool((self.html_code or '').strip())

    @property
    def preview_note(self):
        """One honest sentence about what a visitor can do here."""
        if self.can_run_preview:
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

class CommentReport(models.Model):
    """A visitor flagging a comment — the report button the comment spec
    promised ("Report comment -> CommentReport model").

    5 Whys:
    1. Why a model and not a flag on Comment? A comment can be reported
       several times by different people for different reasons; the
       queue needs the who/why/when, not a boolean.
    2. Why keep the reporter nullable? report_vibe already lets visitors
       report without an account — comments get the same door. Rate
       limiting (IP) is the abuse brake, matching the vibe report view.
    3. Why `resolved` and not delete-on-handle? Moderators need an audit
       trail: hide vs dismiss is a decision someone made, and the row is
       the receipt.
    4. Why CASCADE on comment? The report is metadata about that comment;
       when the comment is legally erased, its reports go with it.
    5. Why SET_NULL on reporter? Deleting an account must not erase the
       fact that a comment was flagged (the queue decision stays valid).
    """
    REASON_CHOICES = [
        ('abusive', 'Abusive or vulgar language'),
        ('spam', 'Spam'),
        ('harassment', 'Harassment'),
        ('other', 'Other'),
    ]
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='reports')
    reporter = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='comment_reports')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='other')
    details = models.CharField(max_length=500, blank=True)
    resolved = models.BooleanField(default=False, help_text='True once a moderator hid or dismissed it')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['resolved', '-created_at'])]

    def __str__(self):
        return f'Report #{self.id} on comment #{self.comment_id} ({self.reason})'


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

class ProjectCoOwner(models.Model):
    """A revenue share in a vibe's star trades.

    The OWNER keeps whatever the co-owners don't: owner_share =
    100 − Σ(co-owner share_percent). Adding or removing a co-owner never
    rewrites existing rows — the remainder always rebalances itself.

    5 Whys:
    1. Why percentages, not absolute star amounts? Star cost changes over
       time (0 → 3 → 5); a percentage stays fair at every price point.
    2. Why a separate table instead of a second owner FK on AppProject?
       owner is non-null everywhere (templates, ranks, lifecycle ghost).
       The table keeps owner = accountable entity; money splits are a
       separate, removable attribute.
    3. Why CASCADE on project? The row is pure metadata about the project;
       a hard project delete (never paid) should take the split with it.
    4. Why CASCADE on user? A co-owner leaving the platform removes their
       share automatically — their percentage silently returns to the owner.
    5. Why cap each share at 100 and validate the sum? The form enforces
       Σ ≤ 100 so the owner's remainder never goes negative; the Check
      Constraint guards direct writes.
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

    def save(self, *args, **kwargs):
        # ORM/admin backstop. The edit view tells the author when their
        # changelog is rejected; this catch is for writes that skipped it
        # (shell, admin, git push snapshots).
        from .profanity import contains_profanity
        if contains_profanity(self.changelog):
            self.changelog = 'Update'
        super().save(*args, **kwargs)

    def __str__(self): return f"{self.project.slug} v{self.version}"

class Trade(models.Model):
    """Star trading — a money record.

    5 Whys on the FK rules:
    1. Why PROTECT the project? A hard project delete used to cascade
       every buyer's receipt AND their paid download. Money records must
       outlive content — content soft-deletes instead (status='removed').
    2. Why SET_NULL on buyer/seller? Deleting *your* account must not
       delete the *other* party's receipt.
    3. Why nullable users but a protected project? The project row carries
       the ZIP buyers paid for; a user row carries nothing the counterparty
       needs.
    4. Why NO unique (buyer, project) anymore? Co-owner splits create one
       Trade row PER RECIPIENT (owner + each co-owner) so ranks, payout
       dashboards and ledger refs each see exactly their share. "One
       purchase per buyer" is now enforced in code (the early-return guard
       + the re-check under the project lock in trade_for_download), not
       by the schema — and the project-row lock serializes concurrent
       purchases, which is stronger than letting one INSERT fail.
    5. Why not soft-delete users too? Django auth deletion is a legal
       (POPIA) erasure path; receipts just stop naming them.
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

# Deploy model removed. 5 Whys: Why delete instead of keep? It promised
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
        ('moderation', 'Moderation'),
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

    5 Whys — why a rolled-up score table instead of ranking from raw events?

    1. Why not just query VibeView/Star/Trade at feed time? Ranking a page
       would need a per-user aggregate over the whole event history on every
       request. At "tons of uploads a second" scale the read path is the hot
       path; it must touch a bounded number of rows.
    2. Why not cache that aggregate instead? A cache still has to be built
       from the events on a miss, so the worst case (cold cache, big user)
       is unchanged. A materialised row has no cold case.
    3. Why per-kind rather than per-project embeddings? 14 buckets is the
       smallest thing that can express "push games to the front", which is
       the actual request. Per-project similarity is a different, far more
       expensive product and is not needed to answer it.
    4. Why keep `score` decayed rather than a raw count? Taste changes. A
       user who played games in March and now ships APIs should see APIs;
       an undecayed counter would keep them on games forever.
    5. Why store `updated_at` per row instead of decaying on a schedule?
       Lazy decay-on-read means no periodic job over every user, and a
       dormant user's row is decayed correctly the moment they return.
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

    5 Whys:
    1. Why a log table instead of charting `AppProject.clones`? That counter
       is cumulative with no timestamps — a "clones/day" chart drawn from it
       would be fiction. This table is the one append-only time series the
       clone metric actually has.
    2. Why both a counter AND rows? Cards/ranks need the cheap integer; the
       dashboard needs the history. `record_clone` writes both in the same
       code path so they can never drift.
    3. Why `source` ('git' | 'zip')? A ZIP download and a `git clone` are
       different behaviours — the chart can show them separately, and the
       git daemon throttles only its own rows (a retried pack transfer must
       not mint a clone per retry).
    4. Why an ip_hash for anonymous rows only? Git clones are throttled per
       actor per project per hour; authenticated users are keyed by user,
       anonymous by a SHA-256 of the IP — no raw IP is stored, and the hash
       is used only for that throttle and the anonymous slice of the chart.
    5. Why CASCADE on project but SET_NULL on user? Clone history belongs to
       the vibe; if the account goes away (POPIA erasure), the row stays as
       an anonymous event instead of vanishing from the chart.
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
