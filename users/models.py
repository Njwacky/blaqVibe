from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .footer_contacts import (
    build_href,
    contact_kind_choices,
    display_label,
    icon_for,
    kind_label,
    normalize_value,
)

# Name style whitelists (rendered by Profile.name_style_css)
NAME_FONTS = {
    'classic': '',                                    # inherit — free default
    'grotesk': '"Space Grotesk","Inter",sans-serif',  # site display face
    'mono': '"JetBrains Mono",ui-monospace,Menlo,monospace',
    'serif': 'Georgia,"Times New Roman",serif',
    'rounded': 'ui-rounded,"Segoe UI",Verdana,sans-serif',
}
NAME_COLORS = {
    'default': '',          # inherit — free default
    'violet': '#7C3AED',    # brand
    'gold': '#f5c518',
    'cyan': '#22d3ee',
    'crimson': '#ef4444',
    'emerald': '#34d399',
    'rainbow': '',          # animated gradient class, not a hex
}
NAME_SIZES = {'md': '', 'lg': 'name-size-lg', 'xl': 'name-size-xl'}
NAME_FX = {
    'none': '',            # free default
    'glow': 'namefx-glow',
    'shine': 'namefx-shine',   # animated light sweep — the "anime" flex
    'chroma': 'namefx-chroma', # slow hue rotation
}
# Pretty labels for the picker (users/forms.py). Choices are built FROM the
# whitelists so the form can never offer — or accept — a slug the renderer
# doesn't know. Hand-written <option>s in a template would drift: a slug the
# renderer drops silently becomes a paid no-op and a support ticket.
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

# Twenty named people-styles
# Classic is the free default (already on every Profile). It is NOT one of
# the twenty — counting it would advertise a 20th look that costs nothing
# and does nothing. The twenty are Coder, Glamour, Charmer, Strict and
# sixteen more. Each recipe is a packed NAME_* tuple + a versioned CSS class.
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
    # In-app notification preferences
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
    # Name style (users/rename.py is the only writer)
    # Stored as whitelisted slugs, rendered via NAME_* dicts — never raw CSS.
    name_font = models.CharField(max_length=20, default='classic')
    name_color = models.CharField(max_length=20, default='default')
    name_size = models.CharField(max_length=4, default='md')
    name_fx = models.CharField(max_length=20, default='none')
    # Named people-style (coder / glamour / …). Classic is the free default.
    # users/rename.set_name_style is the only writer; compose_name_style is
    # the only reader. Unknown slugs coerce to classic.
    name_persona = models.CharField(max_length=20, default='classic')
    last_rename_at = models.DateTimeField(null=True, blank=True, help_text='Set by rename_user — the 30-day cooldown anchor')
    # Git daemon credential for social-login users (no password on file).
    # Only the SHA-256 is stored; the plaintext is shown once at rotate. A
    # token is needed because `git push` uses Basic auth and GitHub/Gmail users
    # have no usable Django password; hashing keeps a DB dump from exposing it.
    # It is high-entropy (token_urlsafe) and compared with compare_digest, so a
    # fast hash suffices — bcrypt's strength is against low-entropy secrets.
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

    # These delegate to the User rather than a Profile field: follow rows
    # point at User on both ends, so the 'followers'/'following' managers live
    # on User — reading self.followers raised AttributeError. The User is the
    # single source of truth.
    def followers_count(self): return self.user.followers.count()
    def following_count(self): return self.user.following.count()
    def vibes_count(self): return self.user.projects.filter(status='published').count()

    @property
    def initial(self) -> str:
        """First letter of the username — the no-photo avatar tile.

        Shown anywhere the profile has no uploaded picture (profile
        header, nav, leaderboard, comments…). '?' when the name is blank.
        """
        name = (self.user.username or '').strip()
        return name[0].upper() if name else '?'

    @property
    def frame_tier(self) -> str:
        """Avatar-frame tier ('bronze' … 'platinum') from total stars.

        Same thresholds as rank — the ring around the picture always
        agrees with the rank badge. Blank when unknown (no frame).
        """
        if getattr(self, '_frame_tier_cache', None) is None:
            from gallery.ranks import frame_for_user
            self._frame_tier_cache = frame_for_user(self.user)
        return self._frame_tier_cache

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
        # Memoised for the life of this instance (one request). A single
        # page renders a creator's totals in three or four places — the
        # profile header, the owner card, the co-owner row — and each one
        # used to run its own SUM(). Writers (stars, trades) bump rows with
        # F() and never re-read these totals in the same request, so the
        # cached value cannot be stale on the page that computed it.
        if getattr(self, '_stars_received_cache', None) is None:
            from django.db.models import Sum
            self._stars_received_cache = self.user.projects.aggregate(s=Sum('stars'))['s'] or 0
        return self._stars_received_cache
    def rank(self):
        from gallery.ranks import contributor_bonus
        return contributor_bonus(self.user)

    # Styled name rendering (read-side of the NAME_* / NAME_PERSONAS whitelists).
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


class ProfileLink(models.Model):
    """One of a builder's own websites — a row, not a field.

    5 Whys: why rows instead of the old single `Profile.website` field?
    1. Why change it? One URLField could only ever hold one site. Builders
       ship a portfolio, a blog, a SaaS, a lab — the profile must show all
       of them, each with its own display name.
    2. Why a status on the row? This app is for developers who never publish
       anything: sites rot, move and go dark. A small traffic-light icon
       (green = live, orange = under maintenance, grey = inactive) tells the
       visitor what they will find BEFORE they click.
    3. Why a `moved_to` column? When a site moves, the old address dies but
       the audience does not. Marking the row `moved` re-points the chip at
       the new address so the profile never advertises a dead link.
    4. Why a position? The order of someone's links is a judgement call —
       portfolio first, then blog — not alphabetical luck.
    5. Why max 12? A profile is an identity card, not a link farm. 12 rows
       is more than any honest builder needs and keeps the page (and bots)
       honest. Enforced in the formset (validate_max) and clean() here.
    """

    STATUS_ACTIVE = 'active'
    STATUS_MAINTENANCE = 'maintenance'
    STATUS_INACTIVE = 'inactive'
    STATUS_MOVED = 'moved'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_MAINTENANCE, 'Under maintenance'),
        (STATUS_INACTIVE, 'Inactive'),
        (STATUS_MOVED, 'Moved'),
    ]
    MAX_LINKS = 12

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='links')
    label = models.CharField(max_length=60, help_text='Display name — e.g. Portfolio, Blog, Side project')
    url = models.URLField(max_length=300)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    moved_to = models.URLField(max_length=300, blank=True, help_text='Only for Moved — where the site lives now')
    position = models.PositiveIntegerField(default=0, help_text='Lower shows first')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', 'id']
        constraints = [
            models.CheckConstraint(
                check=~models.Q(label=''),
                name='profilelink_label_nonempty',
            ),
        ]

    def __str__(self):
        return f'{self.label} → {self.url} ({self.status})'

    def clean(self):
        # A "moved" row that does not say where it moved to would render a
        # chip that lies twice (dead URL, no destination). Enforced here —
        # not in the form — so admin and any future writer inherit it, and
        # ModelForm surfaces it once through _post_clean.
        if self.status == self.STATUS_MOVED and not (self.moved_to or '').strip():
            raise ValidationError({'moved_to': 'A moved link needs its new address.'})
        if self.status == self.STATUS_MOVED and (self.moved_to or '').strip() == (self.url or '').strip():
            # "Moved" pointing at itself is not a move — the visitor clicks
            # the chip and lands on the same dead page.
            raise ValidationError({'moved_to': 'The new address must differ from the old URL.'})
        # A status that is NOT moved must not keep a stale moved_to around —
        # otherwise a later flip to active would silently redirect visitors.
        if self.status != self.STATUS_MOVED:
            self.moved_to = ''

    def save(self, *args, **kwargs):
        self.full_clean(exclude=['profile'])  # backstop: no writer can bypass clean()
        super().save(*args, **kwargs)

    # ── Read-side helpers the templates use ──
    @property
    def is_moved(self) -> bool:
        return self.status == self.STATUS_MOVED

    @property
    def is_clickable(self) -> bool:
        """Inactive sites are announced, not linked — the icon is grey."""
        return self.status != self.STATUS_INACTIVE

    @property
    def href(self) -> str:
        """Where the chip actually points: moved rows point at the new home."""
        if self.is_moved and self.moved_to:
            return self.moved_to
        return self.url

    @property
    def dot_class(self) -> str:
        """CSS modifier for the small status icon on the profile chip."""
        return {
            self.STATUS_ACTIVE: 'link-dot--active',
            self.STATUS_MAINTENANCE: 'link-dot--maintenance',
            self.STATUS_INACTIVE: 'link-dot--inactive',
            self.STATUS_MOVED: 'link-dot--moved',
        }.get(self.status, 'link-dot--inactive')

    @property
    def status_label(self) -> str:
        return dict(self.STATUS_CHOICES).get(self.status, 'Inactive')

    @property
    def chip_class(self) -> str:
        return {
            self.STATUS_ACTIVE: 'profile-link--active',
            self.STATUS_MAINTENANCE: 'profile-link--maintenance',
            self.STATUS_INACTIVE: 'profile-link--inactive',
            self.STATUS_MOVED: 'profile-link--moved',
        }.get(self.status, 'profile-link--inactive')


class SiteSettings(models.Model):
    """Singleton global settings managed through authenticated operator pages."""
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

class FooterContact(models.Model):
    """One public way to reach BlaqVibes — a row, not a field.

    5 Whys: why rows instead of three fields on SiteSettings?
    1. Why change it? The three fields could only ever hold one email, one
       GitHub URL and its label. Two support mailboxes, a WhatsApp number and
       an X account needed a model change, a migration, a form change and a
       deploy each time the company added a way to be reached.
    2. Why a row per method? "Add another" is then a save in the admin page —
       no code path changes shape when the count goes from 1 to 5.
    3. Why a kind + value instead of storing the href? The link scheme is a
       security boundary. A stored href could be javascript:; a built one can
       only be mailto:, tel:, wa.me, a host we chose, or a validated http(s)
       URL (see users/footer_contacts.py).
    4. Why a position and an is_active flag? Operators pause a channel they no
       longer monitor before they delete it, and the order of the footer is a
       judgement call, not alphabetical luck.
    5. Why keep the admin page as the only editor? Because the values are
       public on every page: one gate, one audit row (AdminLog), one rate
       limit — the same posture the old SiteSettings form had.
    """
    kind = models.CharField(max_length=24, choices=contact_kind_choices, default='email')
    value = models.CharField(
        max_length=200,
        help_text='The address, number, handle or URL — never a full link you type by hand.',
    )
    label = models.CharField(
        max_length=80,
        blank=True,
        help_text='Optional. What visitors read instead of the raw address, number or handle.',
    )
    position = models.PositiveSmallIntegerField(default=0, help_text='Lower numbers appear first.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('position', 'id')
        verbose_name = 'footer contact'
        verbose_name_plural = 'footer contacts'

    def __str__(self):
        return f'{self.kind_label}: {self.display_label}'

    @property
    def href(self) -> str:
        return build_href(self.kind, self.value)

    @property
    def icon(self) -> str:
        return icon_for(self.kind)

    @property
    def kind_label(self) -> str:
        return kind_label(self.kind)

    @property
    def display_label(self) -> str:
        return display_label(self.kind, self.value, self.label)

    @property
    def external(self) -> bool:
        """http(s) links open in a new tab; mailto:/tel: stay in the page."""
        return self.href.startswith('http')

    def clean(self):
        # Runs for the operator page AND the Django admin, because ModelForm
        # validation calls instance.full_clean(). Errors raised with a field
        # key land on that field, not in non-field errors.
        super().clean()
        try:
            self.value = normalize_value(self.kind, self.value)
        except ValidationError as exc:
            raise ValidationError({'value': exc.messages}) from exc

    def save(self, *args, **kwargs):
        # Canonical storage: 'Support@Example.com ' → 'support@example.com',
        # '082 555 0100' → '+27825550100'. The form already reported any error,
        # so a row created outside a form keeps its value rather than raising.
        try:
            self.value = normalize_value(self.kind, self.value)
        except ValidationError:
            pass
        if self.position is None:
            self.position = 0
        return super().save(*args, **kwargs)


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

# The one-time welcome grant is paid when the email is verified, not at signup:
# signup is free and scriptable, while a mailbox is the first scarce resource
# we can bind currency to.
WELCOME_STARS = 5

class StarEvent(models.Model):
    """Append-only star ledger — every wallet move is a row.
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

# Money architecture: BUYER → PAY → BLAQVIBES → UNLOCK PROJECT. Stars are the
# platform economy (reputation, unlocks, competition) and are NOT redeemable
# for ZAR — there is no star→ZAR rate and no creator cash-out program.

# Identity money rules (users/rename.py is the only writer)
# PUBG rule: a name is not free to change. Pro accounts carry a rename card;
# everyone else burns stars. Star sinks live next to the ledger they debit.
RENAME_COST_STARS = 100     # a rename card — 10× the welcome grant, not farmable
STYLE_COST_STARS = 20       # restyle your display name — cosmetic sink
RENAME_COOLDOWN_DAYS = 30   # PUBG-style cooldown: one rename per window, no exceptions
RENAME_RESERVE_DAYS = 90    # your old name stays reserved (anti-impersonation)

class UsernameHistory(models.Model):
    """Every completed rename — the reservation list AND the redirect map.
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
    """LEGACY table — kept only so historical rows survive migrations.

    BlaqVibes no longer runs a creator cash-out program and never promises
    creators money: BUYER → PAY → BLAQVIBES → UNLOCK PROJECT. No code path
    writes new rows here; the model stays temporarily for data safety and
    can be dropped in a later migration once the history is archived.
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

# ---------------------------------------------------------------------------
# Account quarantine — the 30-day hold for people who break the rules.
#
# Two different words, two different things, on purpose:
#   * AppProject.status == 'quarantined'  → one VIBE (virus / secrets), and
#     the owner is told to fix the bytes.
#   * UserQuarantine                      → one ACCOUNT (offensive language,
#     harassment, spam), and the person is told what they did, how long it
#     lasts, and how to appeal. This is a people decision, not a file scan.
#
# The models live in `users` because the unit of quarantine is the account.
# Everything that READS or WRITES them goes through `users/quarantine.py`,
# which is the only writer of UserQuarantine.status.
# ---------------------------------------------------------------------------

class RuleViolation(models.Model):
    """One recorded breach of the public rules, with the evidence.

    Why a table instead of a counter? An appeal is a human asking "what did
    I actually say?" — staff need the refused text and where it happened to
    answer that honestly. It is also the audit trail if a quarantine is ever
    questioned, and the raw material for spotting repeat offenders.
    """

    KINDS = [
        ('offensive_language', 'Offensive language'),
        ('harassment', 'Harassment / abuse'),
        ('spam', 'Spam / flooding'),
        ('impersonation', 'Impersonation'),
        ('other', 'Other rule breach'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rule_violations')
    kind = models.CharField(max_length=24, choices=KINDS, default='other', db_index=True)
    # Where it happened, as a slug: comment / review / profile / username /
    # pr / tip / project / ai_readme. Kept small and stable so the staff page
    # can label it without a second lookup table.
    surface = models.CharField(max_length=24, blank=True)
    detail = models.CharField(max_length=300, blank=True)
    # The text that was refused. Staff-only surface (appeals queue) — never
    # rendered publicly, and deliberately never copied into an email or an
    # in-app notification (nobody needs the slur in their inbox).
    evidence = models.TextField(blank=True)
    project_slug = models.CharField(max_length=120, blank=True)
    # True when this violation is what started (or escalated to) a quarantine.
    quarantined = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return f'@{self.user.username} {self.kind} ({self.created_at:%Y-%m-%d})'

class UserQuarantine(models.Model):
    """A 30-day hold on one account: no new public content, appealable.

    What it does NOT do: delete anything, hide the person's existing work, or
    stop them reading/downloading. A quarantine is a pause on *posting*, not
    a deletion — the person keeps their account, and 30 days later it lifts
    by itself. Staff can lift it sooner from the appeals queue.
    """

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('lifted', 'Lifted'),
        ('expired', 'Expired'),
    ]
    SOURCE_CHOICES = [
        ('auto', 'Automatic — rule violation'),
        ('staff', 'Staff decision'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quarantines')
    # Same vocabulary as RuleViolation.KINDS so a staff-written quarantine and
    # an automatic one read identically on the appeals page.
    reason = models.CharField(max_length=24, choices=RuleViolation.KINDS, default='other')
    detail = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active', db_index=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='auto')
    days = models.PositiveSmallIntegerField(default=30)
    # How many rule violations were on record when this hold was applied.
    strike_count = models.PositiveSmallIntegerField(default=1)
    imposed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='quarantines_imposed',
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField(db_index=True)
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='quarantines_lifted',
    )
    lift_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['user', 'status', '-started_at'])]

    def is_active(self, now=None) -> bool:
        """True while the hold is live. Expiry is a clock, not a cron job."""
        now = now or timezone.now()
        return self.status == 'active' and self.ends_at > now

    def days_left(self, now=None) -> int:
        now = now or timezone.now()
        if not self.is_active(now):
            return 0
        seconds = (self.ends_at - now).total_seconds()
        return max(0, -(-int(seconds) // 86400))  # ceil: 0.2 days → 1 day left

    def ends_label(self) -> str:
        """Local date the hold ends, formatted for a human sentence."""
        from django.utils.formats import date_format
        try:
            return date_format(timezone.localtime(self.ends_at), 'j M Y')
        except Exception:
            return self.ends_at.date().isoformat()

    @property
    def reason_label(self) -> str:
        return dict(RuleViolation.KINDS).get(self.reason, self.reason)

    def __str__(self):
        return f'@{self.user.username} {self.reason} until {self.ends_at:%Y-%m-%d} ({self.status})'

class QuarantineAppeal(models.Model):
    """A quarantined person saying "that was a misunderstanding" — in writing.

    One row per attempt, so a refusal can be explained and a later, better
    appeal can be sent without re-creating the quarantine. Staff read every
    one from /moderation/appeals/ and the outcome is told back to the person
    in their inbox (and by email) — never left hanging.
    """

    STATUS_CHOICES = [
        ('open', 'Open — awaiting staff'),
        ('accepted', 'Accepted — quarantine lifted'),
        ('denied', 'Denied — quarantine stands'),
    ]
    # What a moderator can do with an open appeal.
    DECISIONS = [
        ('accept', 'Accept — lift the quarantine now'),
        ('deny', 'Deny — the quarantine stands'),
        ('extend', 'Deny and add another 30 days'),
    ]

    quarantine = models.ForeignKey(UserQuarantine, on_delete=models.CASCADE, related_name='appeals')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quarantine_appeals')
    message = models.TextField(max_length=2000)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='quarantine_appeals_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

    def save(self, *args, **kwargs):
        # The appellant is the quarantined person, always. A caller cannot
        # hand in an appeal that belongs to somebody else's quarantine.
        if self.quarantine_id:
            self.user_id = self.quarantine.user_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f'@{self.user.username} appeal #{self.pk} ({self.status})'

# --------------------------------------------------------------------
# Feedback conversations — the temporary fast path to a human.
#
# The product is still under construction, so every page carries a
# glowing floating button (the .bv-fab in the base template). It opens
# /feedback/: a thread the user writes into, and a superadmin reads in
# their inbox and REPLIES HERE — into the same thread — not to a
# mailbox nobody checks. That is why this is two tables: a thread
# (who is talking, and the read markers) and its messages (who said
# what, in order).
# --------------------------------------------------------------------
class FeedbackThread(models.Model):
    STATUS_CHOICES = [
        ('open', 'Open — waiting on the team'),
        ('answered', 'Answered'),
        ('closed', 'Closed'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='feedback_threads')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    # Newest message FROM THE USER. Drives both the staff-unread calc
    # (admin_last_read_at < last_user_message_at) and queue ordering, so a
    # staff reply can never push its own conversation back to the front.
    last_user_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # When a superadmin last opened this conversation (the read marker).
    admin_last_read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_user_message_at', '-created_at']
        indexes = [models.Index(fields=['status', 'last_user_message_at'])]

    def __str__(self):
        return f'Feedback #{self.pk} — @{self.user.username} ({self.status})'

    def unread_for_staff(self):
        """True while a user message is still waiting on the superadmin."""
        if self.last_user_message_at is None:
            return False
        if self.admin_last_read_at is None:
            return True
        return self.last_user_message_at > self.admin_last_read_at


class FeedbackMessage(models.Model):
    thread = models.ForeignKey(FeedbackThread, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='feedback_messages')
    # Stored at write time: "which side of the conversation is this" must
    # not change if the sender's role changes later — a demoted admin's
    # old reply is still a team reply.
    from_staff = models.BooleanField(default=False)
    body = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['thread', 'created_at'])]

    def __str__(self):
        who = 'team' if self.from_staff else f'@{self.sender.username}'
        return f'{who}: {self.body[:40]}'


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)
