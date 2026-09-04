from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

NAME_FONTS = {
    'classic': '',
    'grotesk': '"Space Grotesk","Inter",sans-serif',
    'mono': '"JetBrains Mono",ui-monospace,Menlo,monospace',
    'serif': 'Georgia,"Times New Roman",serif',
    'rounded': 'ui-rounded,"Segoe UI",Verdana,sans-serif',
}
NAME_COLORS = {
    'default': '',
    'violet': '#7C3AED',
    'gold': '#f5c518',
    'cyan': '#22d3ee',
    'crimson': '#ef4444',
    'emerald': '#34d399',
    'rainbow': '',
}
NAME_SIZES = {'md': '', 'lg': 'name-size-lg', 'xl': 'name-size-xl'}
NAME_FX = {
    'none': '',
    'glow': 'namefx-glow',
    'shine': 'namefx-shine',
    'chroma': 'namefx-chroma',
}
NAME_FONT_LABELS = {
    'classic': 'Classic (default)', 'grotesk': 'Space Grotesk',
    'mono': 'JetBrains Mono', 'serif': 'Serif', 'rounded': 'Rounded',
}
NAME_COLOR_LABELS = {
    'default': 'Default (default)', 'violet': 'Violet', 'gold': 'Gold',
    'cyan': 'Cyan', 'crimson': 'Crimson', 'emerald': 'Emerald',
    'rainbow': 'Rainbow (animated)',
}
NAME_SIZE_LABELS = {'md': 'Normal', 'lg': 'Large', 'xl': 'Extra large'}
NAME_FX_LABELS = {
    'none': 'None (default)', 'glow': 'Glow',
    'shine': 'Anime shine (animated)', 'chroma': 'Chroma shift (animated)',
}

NAME_PERSONAS = {
    'classic': {
        'label': 'Classic',
        'blurb': 'Plain. No flex. Always free.',
        'font': 'classic',
        'color': 'default',
        'size': 'md',
        'fx': 'none',
        'cls': '',
    },
    'coder': {
        'label': 'Coder',
        'blurb': 'Mono, cyan, terminal glow.',
        'font': 'mono',
        'color': 'cyan',
        'size': 'md',
        'fx': 'glow',
        'cls': 'namepersona-coder',
    },
    'glamour': {
        'label': 'Glamour',
        'blurb': 'Serif gold with an anime shine.',
        'font': 'serif',
        'color': 'gold',
        'size': 'lg',
        'fx': 'shine',
        'cls': 'namepersona-glamour',
    },
    'charmer': {
        'label': 'Charmer',
        'blurb': 'Soft rounded letters, violet sweep.',
        'font': 'rounded',
        'color': 'violet',
        'size': 'md',
        'fx': 'shine',
        'cls': 'namepersona-charmer',
    },
    'strict': {
        'label': 'Strict',
        'blurb': 'Wide, uppercase, no decoration.',
        'font': 'grotesk',
        'color': 'default',
        'size': 'md',
        'fx': 'none',
        'cls': 'namepersona-strict',
    },
    'hacker': {
        'label': 'Hacker',
        'blurb': 'Green mono, the old-school terminal.',
        'font': 'mono',
        'color': 'emerald',
        'size': 'md',
        'fx': 'glow',
        'cls': 'namepersona-hacker',
    },
    'artist': {
        'label': 'Artist',
        'blurb': 'Serif rainbow that keeps shifting.',
        'font': 'serif',
        'color': 'rainbow',
        'size': 'lg',
        'fx': 'chroma',
        'cls': 'namepersona-artist',
    },
    'gamer': {
        'label': 'Gamer',
        'blurb': 'Loud crimson, ready-up energy.',
        'font': 'grotesk',
        'color': 'crimson',
        'size': 'lg',
        'fx': 'glow',
        'cls': 'namepersona-gamer',
    },
    'scholar': {
        'label': 'Scholar',
        'blurb': 'Small-caps serif. Quiet authority.',
        'font': 'serif',
        'color': 'default',
        'size': 'md',
        'fx': 'none',
        'cls': 'namepersona-scholar',
    },
    'street': {
        'label': 'Street',
        'blurb': 'Tight gold grotesk, no apology.',
        'font': 'grotesk',
        'color': 'gold',
        'size': 'lg',
        'fx': 'glow',
        'cls': 'namepersona-street',
    },
    'romantic': {
        'label': 'Romantic',
        'blurb': 'Italic crimson with a slow shine.',
        'font': 'serif',
        'color': 'crimson',
        'size': 'md',
        'fx': 'shine',
        'cls': 'namepersona-romantic',
    },
    'cyber': {
        'label': 'Cyber',
        'blurb': 'Violet mono, chroma on the edges.',
        'font': 'mono',
        'color': 'violet',
        'size': 'lg',
        'fx': 'chroma',
        'cls': 'namepersona-cyber',
    },
    'royalty': {
        'label': 'Royalty',
        'blurb': 'Wide small-caps, gold, extra large.',
        'font': 'serif',
        'color': 'gold',
        'size': 'xl',
        'fx': 'glow',
        'cls': 'namepersona-royalty',
    },
    'rebel': {
        'label': 'Rebel',
        'blurb': 'Skewed crimson. Does not sit straight.',
        'font': 'grotesk',
        'color': 'crimson',
        'size': 'md',
        'fx': 'none',
        'cls': 'namepersona-rebel',
    },
    'zen': {
        'label': 'Zen',
        'blurb': 'Wide, lowercase, emerald calm.',
        'font': 'rounded',
        'color': 'emerald',
        'size': 'md',
        'fx': 'none',
        'cls': 'namepersona-zen',
    },
    'neon': {
        'label': 'Neon',
        'blurb': 'Cyan grotesk with a double glow.',
        'font': 'grotesk',
        'color': 'cyan',
        'size': 'lg',
        'fx': 'glow',
        'cls': 'namepersona-neon',
    },
    'vintage': {
        'label': 'Vintage',
        'blurb': 'Gold small-caps, old-print serif.',
        'font': 'serif',
        'color': 'gold',
        'size': 'md',
        'fx': 'none',
        'cls': 'namepersona-vintage',
    },
    'sport': {
        'label': 'Sport',
        'blurb': 'Extra-large, uppercase, match-day.',
        'font': 'grotesk',
        'color': 'crimson',
        'size': 'xl',
        'fx': 'none',
        'cls': 'namepersona-sport',
    },
    'poet': {
        'label': 'Poet',
        'blurb': 'Italic violet serif, a slow shine.',
        'font': 'serif',
        'color': 'violet',
        'size': 'md',
        'fx': 'shine',
        'cls': 'namepersona-poet',
    },
    'mogul': {
        'label': 'Mogul',
        'blurb': 'Gold grotesk. Quiet money.',
        'font': 'grotesk',
        'color': 'gold',
        'size': 'lg',
        'fx': 'none',
        'cls': 'namepersona-mogul',
    },
    'mystic': {
        'label': 'Mystic',
        'blurb': 'Rounded rainbow that keeps turning.',
        'font': 'rounded',
        'color': 'rainbow',
        'size': 'md',
        'fx': 'chroma',
        'cls': 'namepersona-mystic',
    },
}

_STYLE_DEFAULTS = {
    'font': 'classic',
    'color': 'default',
    'size': 'md',
    'fx': 'none',
}


def people_style_slugs():
    """The twenty named people-styles — Classic is the default, not a person."""
    return [slug for slug in NAME_PERSONAS if slug != 'classic']


def compose_name_style(font='classic', color='default', size='md', fx='none', persona='classic'):
    """Resolve a posted (or stored) style to a safe packed dict.

    5 Whys — one composer for write AND read (each Why has four points):
    1. Why one function instead of set_name_style + Profile renderers?
       a. Two copies of ".get() or default" drift the first time a slug is
          added to one side only — a paid style that renders as plain.
       b. The no-JS path (persona posted, dropdowns still default) and the
          JS path (persona + filled dropdowns) must land on the same row.
       c. Tests call this helper directly; a view-only resolver would leave
          the wallet writer unproven.
       d. The Edit Profile preview maps are built from the same recipes
          this function applies, so the picker cannot sell a look the
          writer refuses.
    2. Why coerce unknown slugs instead of raising?
       a. The renderer must survive a tampered DB value (test_tampered_db).
       b. A direct caller (admin tool, future API) must not crash the
          request after the user already paid.
       c. Fail-closed to Classic is the same decision NAME_FONTS already
          made — one policy, every field.
       d. Raising here would turn a stale slug after a future rename of a
          recipe into a 500 on every profile that still stored it.
    3. Why apply the recipe when the four fields are still defaults?
       a. That is the no-JS card click: radio=coder, dropdowns untouched.
       b. Applying the recipe makes the stored row match the card, so the
          next GET shows the same look without JavaScript.
       c. A tampered font on a coder row heals back to the coder recipe
          instead of rendering a broken half-style.
       d. Defaults + Classic stays Classic — we do not invent a people-style
          the user did not pick.
    4. Why clear the persona when the four fields no longer match?
       a. A leftover namepersona-coder class on a gold-serif mix is a lie.
       b. Flourish CSS (uppercase, skew) would fight the mix they just chose.
       c. Classic + mix is the documented custom path; keeping the slug
          would make the radio lie on the next Settings GET.
       d. Re-selecting the card (defaults or exact recipe) restores it —
          clearing is not a lock-out and does not need a refund.
    5. Why return css/classes here, not only the five slugs?
       a. Profile.name_style_css/_classes become one-liners — no second
          place that can forget the persona class.
       b. Settings cards preview with the exact same strings the profile
          will print.
       c. Tests can assert the packed class list without constructing a
          Profile row.
       d. Nothing user-typed is in the return value; every string came
          from NAME_* / NAME_PERSONAS.
    """
    font = font if font in NAME_FONTS else 'classic'
    color = color if color in NAME_COLORS else 'default'
    size = size if size in NAME_SIZES else 'md'
    fx = fx if fx in NAME_FX else 'none'
    persona = persona if persona in NAME_PERSONAS else 'classic'

    if persona != 'classic':
        recipe = NAME_PERSONAS[persona]
        posted = {'font': font, 'color': color, 'size': size, 'fx': fx}
        recipe_pack = {
            'font': recipe['font'],
            'color': recipe['color'],
            'size': recipe['size'],
            'fx': recipe['fx'],
        }
        if posted == _STYLE_DEFAULTS or posted == recipe_pack:
            font = recipe['font']
            color = recipe['color']
            size = recipe['size']
            fx = recipe['fx']
        else:
            persona = 'classic'

    css = []
    font_css = NAME_FONTS.get(font, '')
    if font_css:
        css.append(f'font-family:{font_css};')
    color_css = NAME_COLORS.get(color, '')
    if color_css:
        css.append(f'color:{color_css};')

    classes = []
    if color == 'rainbow':
        classes.append('namefx-rainbow')
    size_cls = NAME_SIZES.get(size, '')
    if size_cls:
        classes.append(size_cls)
    fx_cls = NAME_FX.get(fx, '')
    if fx_cls:
        classes.append(fx_cls)
    persona_cls = NAME_PERSONAS.get(persona, {}).get('cls') or ''
    if persona_cls:
        classes.append(persona_cls)

    return {
        'name_font': font,
        'name_color': color,
        'name_size': size,
        'name_fx': fx,
        'name_persona': persona,
        'css': ''.join(css),
        'classes': ' '.join(classes),
    }


def name_style_preview_maps():
    """Whitelist payload for the Edit Profile live preview (json_script)."""
    return {
        'fonts': NAME_FONTS,
        'colors': NAME_COLORS,
        'sizes': NAME_SIZES,
        'fx': NAME_FX,
        'personas': {
            slug: {
                'font': meta['font'],
                'color': meta['color'],
                'size': meta['size'],
                'fx': meta['fx'],
                'cls': meta['cls'],
                'label': meta['label'],
            }
            for slug, meta in NAME_PERSONAS.items()
        },
    }

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
    notify_on_star = models.BooleanField(default=True)
    notify_on_fork = models.BooleanField(default=True)
    notify_on_follow = models.BooleanField(default=True)
    notify_on_comment = models.BooleanField(default=True)
    notify_on_trade = models.BooleanField(default=True)
    notify_on_milestone = models.BooleanField(default=True)
    show_language = models.BooleanField(default=True)
    allow_forks = models.BooleanField(default=True)
    allow_prs = models.BooleanField(default=True)
    allow_comments = models.BooleanField(default=True)
    allow_reviews = models.BooleanField(default=True)
    name_font = models.CharField(max_length=20, default='classic')
    name_color = models.CharField(max_length=20, default='default')
    name_size = models.CharField(max_length=4, default='md')
    name_fx = models.CharField(max_length=20, default='none')
    name_persona = models.CharField(max_length=20, default='classic')
    last_rename_at = models.DateTimeField(null=True, blank=True, help_text='Set by rename_user — the 30-day cooldown anchor')
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
        if getattr(self, '_stars_received_cache', None) is None:
            from django.db.models import Sum
            self._stars_received_cache = self.user.projects.aggregate(s=Sum('stars'))['s'] or 0
        return self._stars_received_cache
    def rank(self):
        from gallery.ranks import contributor_bonus
        return contributor_bonus(self.user)

    def _composed_name_style(self):
        """Same composer the writer uses — a bad slug can never leak CSS."""
        return compose_name_style(
            self.name_font,
            self.name_color,
            self.name_size,
            self.name_fx,
            getattr(self, 'name_persona', None) or 'classic',
        )

    def name_style_css(self) -> str:
        """Inline CSS for the display name, built ONLY from NAME_* dicts."""
        return self._composed_name_style()['css']

    def name_style_classes(self) -> str:
        """Theme classes: size, fx, rainbow, and the people-style flourish."""
        return self._composed_name_style()['classes']

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
        ('rename_spend', 'Rename — rename card burned'),
        ('style_spend', 'Name style — cosmetic burned'),
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


STARS_PER_ZAR = 10
MIN_PAYOUT_STARS = 500
MAX_PAYOUT_STARS = 50000

RENAME_COST_STARS = 100
STYLE_COST_STARS = 20
RENAME_COOLDOWN_DAYS = 30
RENAME_RESERVE_DAYS = 90


class UsernameHistory(models.Model):
    """Every completed rename — the reservation list AND the redirect map.

    5 Whys:
    1. Why a row at all? "Can I change my username?" needs an answer that is
       cheaper than "read every Notification URL". The row IS the answer:
       who, from what, to what, when, and how it was paid.
    2. Why reserve the OLD username for 90 days? Without a reservation, the
       minute a known creator renames, a stranger grabs the freed handle and
       impersonates them ("it's me, I renamed, send stars"). The reservation
       window is the anti-phishing cooldown for everyone else's memory.
    3. Why CASCADE on user delete, unlike Sale/Trade's SET_NULL? A deleted
       account's name SHOULD become available again — there is no person
       left to impersonate, and the deletion screen promises a clean exit.
       Money records survive deletion; vanity does not.
    4. Why is new_username stored when user.username has the truth? The row
       is a timeline (A→B→C), not a lookup table. Redirects resolve through
       user.username, the single source of truth; storing new_username keeps
       each row self-describing for the audit log and support tickets.
    5. Why db_index on old_username only? Two hot queries exist: "is this
       name reserved?" (old_username__iexact) and "where did oldname go?"
       (old_username__iexact). Both start at old_username; new_username is
       only ever read on rows already found.
    """
    METHOD_CHOICES = [
        ('pro', 'Pro rename card — 0 ★'),
        ('stars', 'Stars — burned'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='username_history')
    old_username = models.CharField(max_length=150, db_index=True)
    new_username = models.CharField(max_length=150)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES)
    cost_stars = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'username history'

    def __str__(self):
        return f'@{self.old_username} → @{self.new_username} ({self.method})'


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


class SecurityEvent(models.Model):
    """Privacy-preserving account-security audit trail."""
    EVENT_CHOICES = [
        ('login_first_device', 'First recognised sign-in'),
        ('login_new_device', 'New device or network sign-in'),
        ('login_recognized_device', 'Recognised sign-in'),
        ('password_changed', 'Password changed'),
        ('sessions_revoked', 'Other sessions revoked'),
        ('git_tokens_revoked', 'Git credentials revoked'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='security_events')
    event = models.CharField(max_length=32, choices=EVENT_CHOICES)
    ip_hash = models.CharField(max_length=64, blank=True)
    device_hash = models.CharField(max_length=64, blank=True)
    detail = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]


class XPEvent(models.Model):
    """One awarded XP grant — append-only and idempotent by (user, reason, ref).

    5 Whys:
    1. Why a row per grant instead of a single `xp` integer? A counter
       cannot answer "why did this level jump?" and cannot be made
       idempotent; a row can, via the unique constraint below.
    2. Why unique (user, reason, ref)? Every award is keyed to the thing
       that earned it (project 12, trade 44, comment 9). A retry, a double
       click, or a re-run of a task hits the same key and is rejected —
       XP farming by repetition is impossible by construction, not by a
       rate limit that can be tuned wrong.
    3. Why append-only (no update path)? Same rule as StarEvent: a
       progression log you can edit is not a log.
    4. Why is `ref` free text and not a FK? One table serves projects,
       trades, PRs and comments; a generic ref keeps it to one index and
       one uniqueness rule instead of four nullable FKs.
    5. Why is `amount` stored rather than derived from `reason` at read
       time? The weights will be tuned; a stored amount keeps history
       honest — past grants keep the value they were paid at.
    """

    REASON_CHOICES = [
        ('publish', 'Published a vibe'),
        ('star_received', 'Received a star'),
        ('fork_received', 'Received a fork/remix'),
        ('comment_given', 'Gave feedback'),
        ('review_given', 'Wrote a review'),
        ('trade_made', 'Traded for a vibe'),
        ('trade_received', 'Someone traded your vibe'),
        ('pr_merged', 'Pull request merged'),
        ('verified', 'Vibe passed the trust scan'),
        ('challenge_win', 'Won a challenge'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='xp_events')
    amount = models.PositiveSmallIntegerField()
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    ref = models.CharField(max_length=120, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'reason', 'ref')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]


class Achievement(models.Model):
    """An earned badge. Awarded only by users.progress.sync_achievements.

    The slug list lives in progress.ACHIEVEMENTS (server-side table); this
    row is the record that it happened, unique per user, so a badge can
    never be earned twice and never awarded by a form or an API call.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='achievements')
    slug = models.CharField(max_length=40)
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'slug')
        ordering = ['-earned_at']

    def __str__(self):
        return f'@{self.user.username} · {self.slug}'


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
