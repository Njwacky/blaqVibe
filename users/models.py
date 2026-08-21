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
    canvas_url = models.URLField(blank=True, help_text="Public canvas/portfolio board URL (Koboyo, Figma, Miro, etc.)")
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    # 0 by default. The 5 ★ welcome grant is paid once, when the email is
    # verified (users.wallet.grant_welcome_stars) — signup alone mints nothing.
    stars_balance = models.PositiveIntegerField(default=0, help_text="Stars to trade — verify your email for the welcome grant, earn more when people trade your vibes")
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
    # Git daemon credential for social-login users (no password on file).
    # ONLY the SHA-256 lives here; the plaintext is shown once at rotate.
    # 5 Whys: Why a token at all? `git push` uses Basic auth — GitHub/Gmail
    # users have no usable Django password. Why hash it? A credential at
    # rest in plaintext is a breach waiting for a DB dump. Why sha256 not
    # bcrypt? It is high-entropy (token_urlsafe) and compared with
    # compare_digest; bcrypt's strength is against low-entropy secrets.
    git_token_hash = models.CharField(max_length=64, blank=True)
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

    # 5 Whys: Why do these delegate to the User, not a Profile field?
    # Follow rows point at User on both ends (follower/following); the
    # related managers ('followers'/'following') live on User. A version of
    # followers_count that read self.followers crashed with AttributeError,
    # so both counts go through user.* — the single source of truth.
    def followers_count(self): return self.user.followers.count()
    def following_count(self): return self.user.following.count()
    def vibes_count(self): return self.user.projects.filter(status='published').count()

    def rotate_git_token(self) -> str:
        """Issue a fresh git credential; returns the plaintext ONCE.

        The caller shows it to the user in a success message. Only the
        SHA-256 hash is stored, and Basic auth checks passwords first —
        the token never shadows a real password.
        """
        import hashlib
        import secrets
        token = 'git_' + secrets.token_urlsafe(24)
        self.git_token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        self.save(update_fields=['git_token_hash'])
        return token
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
    auto_run_enabled = models.BooleanField(default=False, help_text="If On, open the file preview after publish. This is not a Docker host.")
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


# The one-time welcome grant, paid when the email is verified — not at signup.
# 5 Whys: Why on verify, not signup? Signup is free and scriptable; a mailbox
# is the first scarce resource we can bind currency to.
WELCOME_STARS = 5


class StarEvent(models.Model):
    """Append-only star ledger — every wallet move is a row.

    5 Whys:
    1. Why a ledger at all? stars_balance is a single integer. The first
       "I lost 3 ★" support ticket is unanswerable without per-move rows.
    2. Why append-only (no update/delete path)? A ledger you can edit is
       not a ledger. Corrections are new rows with reason='admin_adjust'.
    3. Why write it inside the same transaction as the balance move?
       A crash between UPDATE and INSERT would leave a balance the ledger
       cannot explain — the exact bug the ledger exists to catch.
    4. Why a `ref` string, not a FK? Trades, challenges and welcome grants
       live in different tables. One free-form ref ('trade:12',
       'challenge:week-3') survives even if the referenced row goes away.
    5. Why store delta, not balance_after? Balance is derivable
       (sum of deltas); storing it duplicates state that can drift.
    """
    REASON_CHOICES = [
        ('welcome', 'Welcome grant'),
        ('trade_spend', 'Trade — stars spent'),
        ('trade_earn', 'Trade — stars earned'),
        ('tip_spend', 'Tip — stars sent'),
        ('tip_earn', 'Tip — stars received'),
        ('challenge_bounty', 'Challenge bounty'),
        ('admin_adjust', 'Admin adjustment'),
        ('backfill', 'Ledger backfill'),
        ('payout_hold', 'Payout — stars held for cash-out'),
        ('payout_refund', 'Payout — rejected, stars returned'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='star_events')
    delta = models.IntegerField()
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    ref = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        sign = '+' if self.delta >= 0 else ''
        return f'{self.user} {sign}{self.delta} ★ ({self.reason})'


class Tip(models.Model):
    """A gratitude star transfer — sender's wallet → recipient's wallet.

    Zero-sum on purpose: 5 Whys (why no minting?):
    1. Why can't a tip create stars? The economy rule (gallery/economy.py):
       "any free action that creates currency is farmable with throwaway
       accounts." A tip must MOVE existing stars or it becomes a printer.
    2. Why a row at all, when StarEvent already records both sides? The
       ledger's `ref: tip:<id>` needs a stable anchor, and the profile's
       "Recent tips" / payout "Tips received" lists are receipts, not
       balance math — they render who tipped what and their message.
    3. Why store the message here, not in StarEvent? The ledger stores
       deltas; the message is social content with its own 200-char cap.
    4. Why not let tips count toward rank? Rank = quality signal from
       trade value. Tips are gratitude — visible in Recent tips instead.
    5. Why indexes on recipient? Every public profile page reads
       recent tips by recipient; the hot read gets the index.
    """
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tips_sent')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tips_received')
    amount = models.PositiveIntegerField(help_text='Stars moved from sender to recipient')
    message = models.CharField(max_length=200, blank=True, help_text='Optional note, sanitized on the way in')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['sender', '-created_at']),
        ]

    def __str__(self):
        return f'@{self.sender} → @{self.recipient} {self.amount}★'


# --- Star → ZAR cash-out rules (users/payouts.py is the only writer) -------
# 5 Whys: Why constants here? The rate and minimum are money policy; keeping
# them next to the ledger they debit means anyone touching the money path
# trips over them first. Why whole-ZAR only? Paystack amounts are integer
# cents; a fractional rate would round differently per request size and the
# frozen amount_zar would stop matching what a creator was quoted.
STARS_PER_ZAR = 10          # 10 ★ = R1
MIN_PAYOUT_STARS = 500      # R50 — below this a bank transfer fee eats the payout
MAX_PAYOUT_STARS = 50000    # R5 000 per request — one human-sized EFT, not a whale exit


class Payout(models.Model):
    """A creator cash-out request — stars held, ZAR paid by an admin.

    5 Whys:
    1. Why hold stars at request time instead of at approval? The stars
       must stop being spendable the moment the creator asks for cash,
       or they can trade the same stars to a friend AND receive the EFT.
       The hold is the debit; approval just moves real money.
    2. Why refund as a NEW ledger row ('payout_refund') instead of
       deleting the hold? The ledger is append-only (see StarEvent). A
       rejected payout is a real event; the refund row is its answer.
    3. Why is amount_zar frozen on the row? The rate is policy and can
       change. The creator was quoted R50 for 500 ★; paying tomorrow's
       rate is a different sale (same rule as PaymentIntent).
    4. Why plain bank fields, not a vaulted wallet? There is no Paystack
       recipient storage yet; a bank name + account number is exactly
       what a human needs to type an EFT, and the row is only shown to
       the creator and money admins (never public — non-admin pages get
       account_masked).
    5. Why SET_NULL on user, like Sale? A payout is a money record.
       Account deletion must not erase the audit trail of cash that
       left the building; the masked account digits keep it traceable.
    """
    STATUS_CHOICES = [
        ('requested', 'Requested'),
        ('paid', 'Paid'),
        ('rejected', 'Rejected'),
    ]
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='payouts')
    amount_stars = models.PositiveIntegerField(help_text='Stars held from the wallet at request time')
    amount_zar = models.PositiveIntegerField(help_text='ZAR frozen at request — the quoted amount')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='requested')
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=20)
    holder_name = models.CharField(max_length=80)
    admin_note = models.CharField(max_length=200, blank=True, help_text='EFT reference or rejection reason')
    provider_ref = models.CharField(max_length=100, blank=True, help_text='Paystack transfer code when a transfer was initiated')
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='payout_reviews')
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        who = f'@{self.user.username}' if self.user else '(deleted user)'
        return f'{who} {self.amount_stars}★ → R{self.amount_zar} ({self.status})'

    @property
    def account_last4(self):
        num = (self.account_number or '').strip()
        return num[-4:] if len(num) > 4 else num

    @property
    def account_masked(self):
        """What non-money pages may show: bank + last 4 digits only."""
        num = (self.account_number or '').strip()
        if len(num) <= 4:
            return num
        return f'••••{num[-4:]}'


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
