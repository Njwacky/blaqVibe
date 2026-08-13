"""Stars economy — the working money path.

No external API. Buyer spends star_cost, seller receives the same amount,
a Trade row is the receipt, and that Trade unlocks the ZIP.
"""
from django.db import IntegrityError, transaction
from django.db.models import F, Sum

from users.models import Profile

from .access import effective_star_cost
from .models import Trade


class TradeError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def trade_for_download(buyer, project):
    """Atomically spend stars to unlock a ZIP.

    Returns the Trade (existing or new). Returns None when the download is
    free for this buyer (owner, or star_cost == 0). Raises TradeError when
    the buyer cannot pay.
    """
    if not getattr(buyer, 'is_authenticated', False):
        raise TradeError('Sign in to trade stars for this vibe.')
    if not project.zip_file:
        raise TradeError('This vibe has no ZIP to unlock.')
    if project.owner_id == buyer.id:
        return None

    cost = effective_star_cost(project)
    if cost == 0:
        return None

    existing = Trade.objects.filter(buyer=buyer, project=project).first()
    if existing:
        return existing

    try:
        with transaction.atomic():
            try:
                buyer_p = Profile.objects.select_for_update().get(user=buyer)
                seller_p = Profile.objects.select_for_update().get(user=project.owner)
            except Profile.DoesNotExist as exc:
                raise TradeError('Account profile is missing. Refresh and try again.') from exc
            if buyer_p.stars_balance < cost:
                raise TradeError(
                    f'Need {cost} ★ to trade for “{project.title}” — you have {buyer_p.stars_balance} ★. '
                    'Earn stars by publishing vibes that get starred or traded.'
                )
            Profile.objects.filter(pk=buyer_p.pk).update(stars_balance=F('stars_balance') - cost)
            Profile.objects.filter(pk=seller_p.pk).update(stars_balance=F('stars_balance') + cost)
            return Trade.objects.create(
                buyer=buyer,
                seller=project.owner,
                project=project,
                cost=cost,
            )
    except IntegrityError:
        existing = Trade.objects.filter(buyer=buyer, project=project).first()
        if existing:
            return existing
        raise TradeError('Could not complete the trade. Try again.')


def adjust_owner_stars(owner, delta: int):
    """Move stars on the owner when someone stars / unstars their vibe."""
    if not owner or delta == 0:
        return
    if delta > 0:
        Profile.objects.filter(user=owner).update(stars_balance=F('stars_balance') + delta)
        return
    Profile.objects.filter(user=owner, stars_balance__gte=abs(delta)).update(
        stars_balance=F('stars_balance') + delta
    )


def stars_earned(user) -> int:
    return Trade.objects.filter(seller=user).aggregate(total=Sum('cost'))['total'] or 0


def stars_spent(user) -> int:
    return Trade.objects.filter(buyer=user).aggregate(total=Sum('cost'))['total'] or 0
