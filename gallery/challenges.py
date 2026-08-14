"""Challenge winner awards — one shot, submissions only."""
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from users.models import Profile

from .models import AppProject, Challenge

PRO_PRIZE_DAYS = 30


class ChallengeAwardError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def award_challenge_winner(challenge, winner):
    """Mark a tagged, published submission as the winner and pay the bounty once."""
    if winner.status != 'published':
        raise ChallengeAwardError('Only published submissions can win.')
    if not winner.tags.filter(slug=challenge.tag).exists():
        raise ChallengeAwardError('That vibe is not a submission for this challenge.')

    bounty = int(challenge.bounty_stars or 0)
    now = timezone.now()
    prize_until = now + timedelta(days=PRO_PRIZE_DAYS)

    with transaction.atomic():
        locked = Challenge.objects.select_for_update().get(pk=challenge.pk)
        if locked.winner_id:
            raise ChallengeAwardError('This challenge already has a winner.')
        locked.winner = winner
        locked.save(update_fields=['winner'])

        profile = Profile.objects.select_for_update().get(user_id=winner.owner_id)
        Profile.objects.filter(pk=profile.pk).update(stars_balance=F('stars_balance') + bounty)
        # Permanent Pro is is_pro + null pro_until — never shorten that.
        if not (profile.is_pro and profile.pro_until is None):
            updates = {'is_pro': True}
            if not profile.pro_since:
                updates['pro_since'] = now
            if not profile.pro_until or profile.pro_until < prize_until:
                updates['pro_until'] = prize_until
            Profile.objects.filter(pk=profile.pk).update(**updates)

        if bounty:
            AppProject.objects.filter(pk=winner.pk).update(stars=F('stars') + bounty)
    return locked
