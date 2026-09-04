"""Challenge winner awards — one shot, submissions only.

5 Whys:
1. Why lock the Challenge row? Two admin clicks would mint the bounty twice.
2. Why re-check the tag inside the lock? The tag can be removed between the
   view's first lookup and the write.
3. Why not add the bounty to project.stars? That counter is "people starred
   this". Battle votes already do not inflate it; a prize must not either.
4. Why one Profile update? A second save can fail after stars moved and
   leave Pro ungranted — or shorten permanent Pro.
5. Why an AdminLog + inbox notify? stars_balance is a counter. Without a
   row you cannot answer "why did this wallet jump 10 ★?"
"""
from datetime import timedelta
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from users.models import AdminLog, Profile, StarEvent

from .models import AppProject, Challenge
from .notify import notify

PRO_PRIZE_DAYS = 30


class ChallengeAwardError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def _is_tagged_submission(winner, tag):
    return (
        winner.status == 'published'
        and winner.tags.filter(slug=tag).exists()
    )


def award_challenge_winner(challenge, winner, actor=None):
    """Pay the bounty once to a published, tagged submission."""
    bounty = int(challenge.bounty_stars or 0)
    now = timezone.now()
    prize_until = now + timedelta(days=PRO_PRIZE_DAYS)

    with transaction.atomic():
        locked = Challenge.objects.select_for_update().get(pk=challenge.pk)
        if locked.winner_id:
            raise ChallengeAwardError('This challenge already has a winner.')
        if not _is_tagged_submission(winner, locked.tag):
            raise ChallengeAwardError('That vibe is not a published submission for this challenge.')

        locked.winner = winner
        locked.save(update_fields=['winner'])

        profile = Profile.objects.select_for_update().get(user_id=winner.owner_id)
        updates = {'stars_balance': F('stars_balance') + bounty}
        if not (profile.is_pro and profile.pro_until is None):
            updates['is_pro'] = True
            if not profile.pro_since:
                updates['pro_since'] = now
            if not profile.pro_until or profile.pro_until < prize_until:
                updates['pro_until'] = prize_until
        Profile.objects.filter(pk=profile.pk).update(**updates)
        if bounty:
            StarEvent.objects.create(
                user=winner.owner,
                delta=bounty,
                reason='challenge_bounty',
                ref=f'challenge:{locked.tag}:{winner.slug}',
            )

    notify(
        winner.owner,
        'challenge',
        f'You won “{locked.title}”',
        f'+{bounty} ★ and Pro for {PRO_PRIZE_DAYS} days.',
        winner.get_absolute_url(),
    )
    try:
        from users.progress import award
        award(winner.owner, 'challenge_win', ref=f'challenge:{locked.tag}')
    except Exception:
        logging.getLogger(__name__).exception('challenge xp failed %s', locked.tag)
    if actor is not None:
        AdminLog.objects.create(
            actor=actor,
            action='challenge_award',
            target=f'{locked.tag}:{winner.slug}:+{bounty}',
        )
    return locked
