"""Identity rules — PUBG-style rename cards and name styling.
One module owns every username mutation, same pattern as wallet.py
(every balance move) and payouts.py (every cash-out). The view is a thin
shell; every future caller (admin tool, API) hits the same walls.
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

# Never registrable — by signup OR rename. One shared list rather than a
# signup-only check: "admin"/"support"/"nolo" phishing works wherever the name
# appears, and a rename is a second registration. The words cover the three
# phishing templates that work — official-sounding (admin, staff, security),
# brand (blaqvibes, nolo) and money-path (billing, payouts) handles.
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

    One helper (rather than inline math in the template/view) because the
    settings page, the rename view and tests must all agree on "can I rename
    now"; this is the single truth, same as payouts.py.
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
    # Why an explicit max? UnicodeUsernameValidator checks charset only —
    # the 150 cap is a FORM-level rule. rename_user is callable without any
    # form (admin tool, API), so the cap lives here too. Fail-closed.
    if len(new) > 150:
        raise RenameError('Username must be 150 characters or fewer.')
    if new.lower() == user.username.lower():
        raise RenameError(f'@{new} is already your username.')
    # Same charset gate as signup: UnicodeUsernameValidator + the public
    # language gate. Why both? The validator stops injection-shaped names
    # ("a b<c>"); the profanity gate stops the words bleach cannot see.
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
    # Why iexact, not exact? Django's UserCreationForm rejects case-only
    # duplicates at signup ("Nolo" vs "nolo"); a rename must not become the
    # side door around that rule.
    if User.objects.filter(username__iexact=new).exclude(pk=user.pk).exists():
        raise RenameError(f'@{new} is already taken.')
    if new.lower() in RESERVED_USERNAMES:
        raise RenameError('That username is reserved — pick another.')
    # Reservation window: the handle was someone's identity recently, so it
    # is not free bait. The owner's OWN old name is exempt (taking your own
    # name back impersonates nobody) — but the 30-day cooldown still applies.
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

    Used by profile_view: /u/<oldname>/ 302s to the creator's CURRENT profile
    instead of 404ing. Every past notification, comment mention and DM link
    embeds /u/<name>/, so a rename must not vaporise months of inbound links —
    the history row is already the map, so this is one indexed lookup.

    Resolve to user.username rather than history.new_username: the row is a
    timeline (A->B->C) and the live column is the truth, so following the FK
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
