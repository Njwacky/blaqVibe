"""Creator progression — XP, levels and achievements.

Everything here is server-side. There is no endpoint, form field or API
route that writes XP: `award_xp` is the only writer and it is called from
the code paths that already did the work (publish, star, fork, trade, PR
merge, trust grade, challenge win). That is the anti-abuse design:

5 Whys (how spam is kept out of progression)
1. Why key every grant to a `ref`? A grant for "project 12" can only ever
   exist once — the unique constraint in users.models.XPEvent rejects a
   replay. Repetition cannot mint XP, so no rate limit has to be guessed.
2. Why daily caps on top of that? Commenting and reviewing are legitimately
   repeatable, so they can each be earned a few times a day and then stop.
   The cap is on the *reason*, not the user, so it throttles the farmable
   action without punishing someone publishing ten vibes.
3. Why never award for your own action on your own content? Starring,
   forking and tipping yourself are either blocked outright or skipped by
   the caller, so a second account is not a shortcut.
4. Why achievements derived from the DB rather than granted inline? A badge
   is a fact about real state (a published, verified, traded vibe). If the
   underlying row is deleted, the badge's facts change; deriving it keeps
   the profile honest instead of showing a trophy for nothing.
5. Why is XP not spendable? Stars are the currency. XP is reputation —
   cheap to display, impossible to convert, so there is nothing to farm it
   for except the profile itself.
"""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Achievement, XPEvent

logger = logging.getLogger(__name__)

# XP per reason. Tuning these changes future grants only — stored rows keep
# the amount they were paid at (see XPEvent 5 Whys #5).
XP_BY_REASON = {
    'publish': 20,
    'star_received': 2,
    'fork_received': 10,
    'comment_given': 2,
    'review_given': 3,
    'trade_made': 3,
    'trade_received': 5,
    'pr_merged': 15,
    'verified': 25,
    'challenge_win': 40,
}

# Repeatable actions: max grants per reason per UTC day. Anything not listed
# is bounded by its `ref` alone (one project, one trade, one PR).
DAILY_CAPS = {
    'comment_given': 5,
    'review_given': 3,
}

LEVELS = [
    (0, 'New Creator'),
    (50, 'Builder'),
    (150, 'Maker'),
    (350, 'Creator'),
    (700, 'Rising Star'),
    (1200, 'Pro Builder'),
    (2000, 'Elite Creator'),
]

# Badge table — server-side only. `check` receives the User and returns a
# bool; it must be cheap (one or two indexed counts) because
# sync_achievements may run after any qualifying write.
ACHIEVEMENTS = [
    {'slug': 'first_project', 'label': 'First Project', 'icon': '🚀',
     'desc': 'Published your first vibe.', 'check': lambda u: _published(u) >= 1},
    {'slug': 'first_star', 'label': 'First Star', 'icon': '★',
     'desc': 'Someone starred your vibe.', 'check': lambda u: _stars_received(u) >= 1},
    {'slug': 'first_fork', 'label': 'First Fork', 'icon': '⑂',
     'desc': 'Someone remixed your work.', 'check': lambda u: _forks_received(u) >= 1},
    {'slug': 'first_trade', 'label': 'First Trade', 'icon': '💱',
     'desc': 'Someone traded stars for your vibe.', 'check': lambda u: _trades(u) >= 1},
    {'slug': 'first_pr', 'label': 'First PR', 'icon': '🔀',
     'desc': 'A pull request of yours was merged.', 'check': lambda u: _merged_prs(u) >= 1},
    {'slug': 'verified_creator', 'label': 'Verified Creator', 'icon': '🛡️',
     'desc': 'A vibe of yours passed every scan.', 'check': lambda u: _verified(u) >= 1},
    {'slug': 'challenge_winner', 'label': 'Challenge Winner', 'icon': '🏆',
     'desc': 'Won a BlaqVibes challenge.', 'check': lambda u: _challenge_wins(u) >= 1},
    {'slug': 'remixer', 'label': 'Remixer', 'icon': '🎛️',
     'desc': 'Remixed three vibes.', 'check': lambda u: _remixes(u) >= 3},
    {'slug': 'projects_10', 'label': '10 Projects', 'icon': '📦',
     'desc': 'Published ten vibes.', 'check': lambda u: _published(u) >= 10},
    {'slug': 'stars_100', 'label': '100 Stars', 'icon': '💯',
     'desc': 'Collected 100 stars across your vibes.', 'check': lambda u: _stars_received(u) >= 100},
    {'slug': 'followers_100', 'label': '100 Followers', 'icon': '👥',
     'desc': 'A hundred people follow you.', 'check': lambda u: u.followers.count() >= 100},
]

ACHIEVEMENTS_BY_SLUG = {a['slug']: a for a in ACHIEVEMENTS}


# --- fact helpers (all import gallery lazily: gallery imports users) ------

def _published(user):
    from gallery.models import AppProject
    return AppProject.objects.filter(owner=user, status='published').count()


def _stars_received(user):
    from gallery.models import Star
    return Star.objects.filter(project__owner=user).exclude(user=user).count()


def _forks_received(user):
    from gallery.models import AppProject
    return AppProject.objects.filter(forked_from__owner=user, status='published').count()


def _trades(user):
    from gallery.models import Trade
    return Trade.objects.filter(seller=user).count()


def _merged_prs(user):
    from gallery.models import PullRequest
    return PullRequest.objects.filter(author=user, status='merged').count()


def _verified(user):
    from gallery.models import AppProject
    return AppProject.objects.filter(owner=user, status='published', trust='verified').count()


def _challenge_wins(user):
    from gallery.models import Challenge
    return Challenge.objects.filter(winner__owner=user).count()


def _remixes(user):
    from gallery.models import AppProject
    return AppProject.objects.filter(owner=user, forked_from__isnull=False).count()


# --- XP ------------------------------------------------------------------

def award_xp(user, reason, ref=''):
    """Grant XP once for one real thing. Returns True when it was paid now.

    Never raises: progression must never break the action that earned it
    (a failed badge cannot lose somebody's upload).
    """
    if not user or not getattr(user, 'pk', None):
        return False
    amount = XP_BY_REASON.get(reason)
    if not amount:
        logger.warning('award_xp: unknown reason %r', reason)
        return False
    try:
        cap = DAILY_CAPS.get(reason)
        if cap is not None:
            since = timezone.now() - timedelta(days=1)
            today = XPEvent.objects.filter(
                user=user, reason=reason, created_at__gte=since
            ).count()
            if today >= cap:
                return False
        with transaction.atomic():
            XPEvent.objects.create(user=user, amount=amount, reason=reason, ref=(ref or '')[:120])
        return True
    except IntegrityError:
        # Same (user, reason, ref) — already paid. Idempotent by design.
        return False
    except Exception:
        logger.exception('award_xp failed user=%s reason=%s', getattr(user, 'pk', None), reason)
        return False


def xp_total(user):
    if not user or not getattr(user, 'pk', None):
        return 0
    return XPEvent.objects.filter(user=user).aggregate(t=Sum('amount'))['t'] or 0


def level_for(xp):
    """Level info for a raw XP number — pure, no queries."""
    xp = int(xp or 0)
    index = 0
    for i, (floor, _name) in enumerate(LEVELS):
        if xp >= floor:
            index = i
    floor, name = LEVELS[index]
    nxt = LEVELS[index + 1] if index + 1 < len(LEVELS) else None
    if nxt:
        span = max(1, nxt[0] - floor)
        pct = max(0, min(100, int((xp - floor) / span * 100)))
        to_go = nxt[0] - xp
        next_name = nxt[1]
        next_at = nxt[0]
    else:
        pct, to_go, next_name, next_at = 100, 0, 'Elite Creator', None
    return {
        'index': index + 1,
        'name': name,
        'xp': xp,
        'floor': floor,
        'next': next_at,
        'next_name': next_name,
        'progress': pct,
        'to_next': to_go,
    }


def progress_for(user):
    """xp_total + level in one call for templates."""
    total = xp_total(user)
    info = level_for(total)
    info['badges'] = earned_badges(user)
    return info


def earned_badges(user):
    if not user or not getattr(user, 'pk', None):
        return []
    have = set(Achievement.objects.filter(user=user).values_list('slug', flat=True))
    return [ACHIEVEMENTS_BY_SLUG[s] for s in
            [a['slug'] for a in ACHIEVEMENTS if a['slug'] in have]]


def sync_achievements(user):
    """Award any badge whose facts are now true. Returns newly earned slugs.

    Called after an XP grant, so a badge lands in the same request as the
    action that earned it — the notification and the profile agree.
    """
    if not user or not getattr(user, 'pk', None):
        return []
    have = set(Achievement.objects.filter(user=user).values_list('slug', flat=True))
    earned = []
    for badge in ACHIEVEMENTS:
        if badge['slug'] in have:
            continue
        try:
            ok = bool(badge['check'](user))
        except Exception:
            logger.exception('achievement check failed %s', badge['slug'])
            ok = False
        if not ok:
            continue
        try:
            with transaction.atomic():
                Achievement.objects.create(user=user, slug=badge['slug'])
            earned.append(badge['slug'])
        except IntegrityError:
            continue
        except Exception:
            logger.exception('achievement write failed %s', badge['slug'])
    return earned


def award(user, reason, ref='', notify=True):
    """award_xp + badge sync + one inbox note for a newly earned badge.

    Convenience wrapper so call sites stay one line. `notify` exists so
    bulk/backfill callers can stay quiet.
    """
    paid = award_xp(user, reason, ref=ref)
    try:
        new_badges = sync_achievements(user)
    except Exception:
        logger.exception('sync_achievements failed user=%s', getattr(user, 'pk', None))
        new_badges = []
    if notify and new_badges:
        from gallery.notify import notify as _notify
        for slug in new_badges[:3]:  # never flood the inbox
            badge = ACHIEVEMENTS_BY_SLUG.get(slug)
            if not badge:
                continue
            _notify(
                user,
                'achievement',
                f'{badge["icon"]} {badge["label"]} unlocked',
                badge['desc'],
                f'/u/{user.username}/',
            )
    return paid
