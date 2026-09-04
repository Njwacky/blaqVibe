"""Identity rules — PUBG-style rename cards and name styling.

One module owns every username mutation, same pattern as wallet.py
(every balance move) and payouts.py (every cash-out). The view is a thin
shell; every future caller (admin tool, API) hits the same walls.

5 Whys (why renames cost anything at all):
1. Why can't a user rename for free any time? The username is identity:
   /u/<name>/ profile URLs, /git/<name>/<vibe>.git clone URLs, trade
   receipts, notification links. Free renames make identity disposable —
   disposable identity is how ban evasion and follow-phishing start.
2. Why stars OR Pro, specifically? Pro is the paid path (a rename card
   ships with the pass, like PUBG's). Stars are the earned path — and
   unlike Pro they come from other humans trading your work, so a farm
   of throwaways can never afford one (the 5 ★ welcome grant is 1/20th
   of the cheapest cosmetic).
3. Why BURN the stars instead of paying them to a "house" account?
   Currency moved to a fake account is still in the economy; burned is
   a sink. The ledger has mints (welcome, trades in) and exactly two
   sinks before this (payout holds). Every sink makes every remaining
   star worth more — inflation control, not a hidden fee.
4. Why a 30-day cooldown even for Pro? The price throttles volume, the
   cooldown throttles frequency. Without it, a paid account can cycle
   names hourly to dodge moderation searches and follower blocklists.
   One card per window is the PUBG rule, kept exactly.
5. Why reserve the OLD name for 90 days? Renaming frees a handle.
   A stranger grabbing "@known-creator" mid-rename is phishing with a
   valid account. The reservation outlives the cooldown because
   OTHER people's memory of the old handle outlives it too.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from gallery.profanity import validate_public_text

from .models import (
    RENAME_COOLDOWN_DAYS,
    RENAME_COST_STARS,
    RENAME_RESERVE_DAYS,
    STYLE_COST_STARS,
    Profile,
    StarEvent,
    UsernameHistory,
    compose_name_style,
)

RESERVED_USERNAMES = frozenset({
    'admin', 'administrator', 'root', 'system', 'official', 'team',
    'staff', 'moderator', 'mod', 'support', 'help', 'security',
    'blaqvibes', 'blaqvibe', 'nolo', 'billing', 'payouts', 'api',
})


class RenameError(Exception):
    """User-facing failure. `.message` goes straight to a flash message."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


def cooldown_remaining(profile) -> timedelta | None:
    """Time left on the rename cooldown, or None when a card is usable.

    5 Whys: why a helper instead of inline math in the template/view?
    The settings page, the rename view and tests all must agree on "can I
    rename now"; one function is the one truth, same as payouts.py.
    """
    if not profile.last_rename_at:
        return None
    ready_at = profile.last_rename_at + timedelta(days=RENAME_COOLDOWN_DAYS)
    left = ready_at - timezone.now()
    return left if left > timedelta(0) else None


def validate_new_username(user, new_username) -> str:
    """All rename gates that do not need the profile lock. Returns the
    cleaned username or raises RenameError with a user-facing message."""
    new = (new_username or '').strip()
    if not new:
        raise RenameError('Type the new username you want.')
    if len(new) < 3:
        raise RenameError('Username must be at least 3 characters.')
    if len(new) > 150:
        raise RenameError('Username must be 150 characters or fewer.')
    if new.lower() == user.username.lower():
        raise RenameError(f'@{new} is already your username.')
    try:
        UnicodeUsernameValidator()(new)
    except Exception:
        raise RenameError(
            'Letters, numbers and @/./+/-/_ only — no spaces or symbols.'
        )
    try:
        validate_public_text(new, allow_blank=False)
    except Exception:
        raise RenameError('That username is not allowed. Please pick another.')
    if User.objects.filter(username__iexact=new).exclude(pk=user.pk).exists():
        raise RenameError(f'@{new} is already taken.')
    if new.lower() in RESERVED_USERNAMES:
        raise RenameError('That username is reserved — pick another.')
    cutoff = timezone.now() - timedelta(days=RENAME_RESERVE_DAYS)
    clash = (
        UsernameHistory.objects.filter(
            old_username__iexact=new, created_at__gte=cutoff,
        )
        .exclude(user=user)
        .exists()
    )
    if clash:
        raise RenameError(
            f'@{new} was another member\u2019s name recently and is reserved '
            f'for {RENAME_RESERVE_DAYS} days after their rename.'
        )
    return new


def rename_user(user, new_username) -> UsernameHistory:
    """The ONLY writer of User.username after signup (Django admin aside).

    Atomic: profile lock → cooldown re-check → validation → pay → history
    row → the rename itself. Returns the UsernameHistory receipt.

    5 Whys:
    1. Why select_for_update on Profile? The cooldown anchor AND the wallet
       live on one row; two concurrent rename POSTs must not both read
       "no rename yet" and both pass. The lock serialises them.
    2. Why re-validate inside the transaction? The uniqueness and
       reservation checks race every other rename on the site; the lock
       plus re-check makes the decision, the payment and the rename one
       indivisible step.
    3. Why charge BEFORE touching user.username? If the burn fails
       (insufficient stars) nothing may change; if the rename fails, the
       transaction rolls the burn back. Either both happen or neither.
    4. Why save(update_fields=['username'])? A full save() on the auth User
       would rewrite every column from a possibly stale in-memory instance;
       update_fields writes exactly one column, once.
    5. Why is the ledger reason 'rename_spend' with ref 'old→new'? The row
       must explain the burn without joining anything — support reads
       "−100 ★ rename:coder→coderprime" and the ticket answers itself.
    """
    with transaction.atomic():
        profile = Profile.objects.select_for_update().get(user=user)
        left = cooldown_remaining(profile)
        if left is not None:
            days = max(1, left.days)
            raise RenameError(
                f'Rename card on cooldown — usable again in {days} day'
                f'{"s" if days != 1 else ""}. One rename per '
                f'{RENAME_COOLDOWN_DAYS} days, even on Pro.'
            )
        new = validate_new_username(user, new_username)
        old = user.username
        if profile.is_pro_active:
            method, cost = 'pro', 0
        else:
            method, cost = 'stars', RENAME_COST_STARS
            if profile.stars_balance < cost:
                raise RenameError(
                    f'A rename card costs {cost} ★ — you have '
                    f'{profile.stars_balance} ★. Go Pro for a free card, or '
                    'earn stars when people trade your vibes.'
                )
            Profile.objects.filter(pk=profile.pk).update(
                stars_balance=F('stars_balance') - cost
            )
            StarEvent.objects.create(
                user=user,
                delta=-cost,
                reason='rename_spend',
                ref=f'rename:{old}\u2192{new}',
            )
        history = UsernameHistory.objects.create(
            user=user,
            old_username=old,
            new_username=new,
            method=method,
            cost_stars=cost,
        )
        user.username = new
        user.save(update_fields=['username'])
        profile.last_rename_at = timezone.now()
        profile.save(update_fields=['last_rename_at'])
        return history


def set_name_style(user, font, color, size, fx, persona='classic'):
    """Apply a whitelisted display-name style. Returns (profile, changed).

    PUBG rule, same as renames: styling is Pro (free while active) or
    20 ★ burned per change. Default everything costs nothing — resetting
    to plain must never be paywalled. A named people-style (coder,
    glamour, …) is the same burn as a hand-mixed look.

    5 Whys:
    1. Why pay at all for a cosmetic? The style is the flex other people
       see on their own pages (follower lists, tips, profiles). Free
       styling = every bio a rainbow at once = nobody stands out;
       priced styling = the style itself signals status. That is the
       show-off economy the feature exists for.
    2. Why charge per CHANGE, not per style? An unlock-forever registry is
       a second wallet to reconcile; a per-change burn reuses the ledger
       that already reconciles. Charge on the transition, skip the
       no-op — a user who re-submits the same style pays nothing.
    3. Why 20 ★ and not 100 ★? It must be cheaper than the name itself
       (restyle freely, rename rarely) but 5★-welcome-grant-proof. 20 ★
       is 4 grants — a throwaway farm breaks even on nothing.
    4. Why coerce unknown slugs (including an unknown persona) to
       defaults instead of raising? The blacklist decision is "never
       render what we did not define" (models.NAME_* / NAME_PERSONAS).
       A legacy or tampered value degrades to plain — the safe render
       and the safe store are the same compose_name_style path.
    5. Why the profile lock? Balance burn + style write must be atomic
       with the affordability check, same race as every wallet move.
    """
    packed = compose_name_style(font, color, size, fx, persona)
    clean = {
        'name_font': packed['name_font'],
        'name_color': packed['name_color'],
        'name_size': packed['name_size'],
        'name_fx': packed['name_fx'],
        'name_persona': packed['name_persona'],
    }
    with transaction.atomic():
        profile = Profile.objects.select_for_update().get(user=user)
        changed = any(
            getattr(profile, key) != value for key, value in clean.items()
        )
        if not changed:
            return profile, False
        is_custom = any(
            value != default
            for value, default in (
                (clean['name_font'], 'classic'),
                (clean['name_color'], 'default'),
                (clean['name_size'], 'md'),
                (clean['name_fx'], 'none'),
                (clean['name_persona'], 'classic'),
            )
        )
        if is_custom and not profile.is_pro_active:
            if profile.stars_balance < STYLE_COST_STARS:
                raise RenameError(
                    f'A name restyle costs {STYLE_COST_STARS} ★ — you have '
                    f'{profile.stars_balance} ★. Go Pro to style it free, or '
                    'earn stars when people trade your vibes.'
                )
            Profile.objects.filter(pk=profile.pk).update(
                stars_balance=F('stars_balance') - STYLE_COST_STARS
            )
            StarEvent.objects.create(
                user=user,
                delta=-STYLE_COST_STARS,
                reason='style_spend',
                ref=(
                    f'style:{clean["name_persona"]}/{clean["name_font"]}/'
                    f'{clean["name_color"]}/{clean["name_size"]}/'
                    f'{clean["name_fx"]}'
                ),
            )
        for key, value in clean.items():
            setattr(profile, key, value)
        profile.save(
            update_fields=[
                'name_font', 'name_color', 'name_size', 'name_fx', 'name_persona',
            ]
        )
        return profile, True


def redirect_target_for_old_username(username):
    """If `username` was a recent rename's old name, return its owner.

    Used by profile_view: /u/<oldname>/ 302s to the creator's CURRENT
    profile instead of 404ing. 5 Whys: why redirect at all? Every past
    notification, comment mention and DM link embeds /u/<name>/. A rename
    must not vaporise months of inbound links — the history row is already
    the map, so the fix is one indexed lookup.

    Why resolve to user.username instead of history.new_username? The row
    is a timeline (A→B→C); the live column is the truth. Following the FK
    survives chained renames for free.
    """
    history = (
        UsernameHistory.objects.select_related('user')
        .filter(old_username__iexact=username)
        .first()
    )
    if history and history.user_id and history.user.username.lower() != username.lower():
        return history.user
    return None
