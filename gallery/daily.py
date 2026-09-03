"""Daily challenges — one prompt a day, no AI needed to have one ready.

5 Whys (why a curated pool instead of only the AI generator)
1. Why not rely on `generate_weekly_challenges`? It needs a provider key,
   a running Celery beat and a superadmin to approve the draft. On a fresh
   deploy (and on this very checkout) that means the Challenges tab is
   empty — a promise with nothing behind it.
2. Why deterministic by date? `pool[days_since_epoch % len(pool)]` means
   every server process derives the SAME challenge for the same day with
   no coordination, no beat, and no row to lock.
3. Why a curated pool at all? These prompts are small enough to finish in
   an evening, which is the point: a challenge you cannot finish is a
   challenge you do not enter twice.
4. Why create real Challenge rows? Everything downstream already speaks
   Challenge: the tag, the submissions query, the bounty, the winner
   award. Reusing the model keeps one code path for prizes.
5. Why settle lazily on read instead of a scheduled job? The winner is
   only knowable after the day ends, and the page that shows the result is
   the only place that needs it. A lazy settle also self-heals a deploy
   that missed its beat.
"""
import logging
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from .models import AppProject, Challenge

logger = logging.getLogger(__name__)

# The rotation anchor. Any fixed past date works: what matters is that
# (today - EPOCH).days is the same number on every process, so everyone
# derives the same prompt for the same day without talking to each other.
EPOCH = date(2026, 1, 1)

# Small, finishable, kind-diverse prompts. Rotate by day.
DAILY_POOL = [
    {'title': 'Build a calculator', 'kind': 'webapp',
     'description': 'A calculator that actually works: +, −, ×, ÷, a clear button, and keyboard support.'},
    {'title': 'Weather card', 'kind': 'webapp',
     'description': 'A card that shows the weather for one city. Mock the data if you have no API key.'},
    {'title': 'Landing page in 60 minutes', 'kind': 'website',
     'description': 'One screen: a headline, one promise, one button. Make the button do something.'},
    {'title': 'A tiny game', 'kind': 'game',
     'description': 'Clicker, snake, or guess-the-number — one mechanic, done properly.'},
    {'title': 'Useful Python tool', 'kind': 'cli',
     'description': 'A command-line tool that saves you a minute a day: rename files, resize images, clean CSV.'},
    {'title': 'Fix a broken app', 'kind': 'webapp',
     'description': 'Take a vibe that does not quite work and fix it. Say in the README what was wrong.'},
    {'title': 'Remix someone', 'kind': 'other',
     'description': 'Fork another creator’s vibe and make one clear improvement. Credit them in the README.'},
    {'title': 'Data dashboard', 'kind': 'dashboard',
     'description': 'Three cards, one chart, one table. Fake the numbers — make the layout real.'},
    {'title': 'To-do that persists', 'kind': 'webapp',
     'description': 'A to-do list that remembers tasks after a refresh (localStorage is fine).'},
    {'title': 'Mobile-first profile', 'kind': 'mobile',
     'description': 'A profile screen that looks right on a phone first, desktop second.'},
    {'title': 'API with two routes', 'kind': 'api',
     'description': 'A tiny API: one route that writes, one that reads. Document both in the README.'},
    {'title': 'Chat UI', 'kind': 'webapp',
     'description': 'A chat window with bubbles, timestamps and typing indicator. No server needed.'},
    {'title': 'Browser extension', 'kind': 'extension',
     'description': 'One small extension that changes one page you use every day.'},
    {'title': 'Make it accessible', 'kind': 'other',
     'description': 'Take any vibe and make it keyboard-navigable with real labels. Explain what you changed.'},
]

DAILY_BOUNTY = 15


def pool_entry_for(day):
    """Same date → same prompt, on every process, forever."""
    index = (day - EPOCH).days % len(DAILY_POOL)
    return index, DAILY_POOL[index]


def daily_tag(day):
    return f'daily-{day.isoformat()}'


def ensure_daily_challenge(today=None):
    """Create today's Challenge row if it does not exist. Idempotent."""
    try:
        today = today or timezone.localdate()
        tag = daily_tag(today)
        existing = Challenge.objects.filter(tag=tag).first()
        if existing:
            return existing
        _index, entry = pool_entry_for(today)
        start = timezone.make_aware(datetime.combine(today, time.min))
        challenge, _created = Challenge.objects.get_or_create(
            tag=tag,
            defaults={
                'title': f'Today: {entry["title"]}',
                'description': entry['description'],
                'bounty_stars': DAILY_BOUNTY,
                'start': start,
                'end': start + timedelta(days=1),
                'is_active': True,
            },
        )
        return challenge
    except Exception:
        logger.exception('ensure_daily_challenge failed')
        return None


def today_challenge():
    try:
        challenge = ensure_daily_challenge()
        if not challenge:
            return None
        challenge.submission_count = submissions(challenge).count()
        challenge.top_submissions = leaderboard(challenge, limit=3)
        return challenge
    except Exception:
        logger.exception('today_challenge failed')
        return None


def submissions(challenge, limit=50):
    """Published vibes tagged for this challenge — newest first."""
    return (
        AppProject.objects.filter(status='published', tags__slug=challenge.tag)
        .select_related('owner', 'owner__profile')
        .order_by('-stars', 'created_at')[:limit]
    )


def leaderboard(challenge, limit=10):
    return list(submissions(challenge, limit=limit))


def settle_past_challenges(limit=7):
    """Award winners for finished days that have submissions but no winner.

    Highest stars wins; the earliest publish breaks a tie (a tie-break that
    rewards shipping early rather than arguing about it). Returns the list
    of (challenge, winner) pairs paid out now.
    """
    settled = []
    try:
        now = timezone.now()
        open_ended = (
            Challenge.objects.filter(is_active=True, winner__isnull=True, end__lt=now)
            .order_by('-end')[:limit]
        )
        for challenge in open_ended:
            entries = leaderboard(challenge, limit=1)
            if not entries:
                # Nothing was submitted: close the day out quietly instead
                # of leaving it as a promise that never resolves.
                challenge.is_active = False
                challenge.save(update_fields=['is_active'])
                continue
            winner = entries[0]
            try:
                from .challenges import award_challenge_winner
                award_challenge_winner(challenge, winner)
                settled.append((challenge, winner))
            except Exception:
                logger.exception('auto-settle failed for %s', challenge.tag)
    except Exception:
        logger.exception('settle_past_challenges failed')
    return settled
