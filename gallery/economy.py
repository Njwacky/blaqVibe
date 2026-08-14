"""Stars economy — the working money path.

No external API. Buyer spends star_cost, seller receives the same amount,
a Trade row is the receipt, a pair of StarEvent rows is the ledger, and
that Trade unlocks the ZIP.

5 Whys (why starring does NOT move the wallet):
1. Why did it before? "Engagement should reward creators" — a star paid
   the owner +1 spendable ★.
2. Why is that a mint? Starring is free and reversible. Unstar only
   deducted when the balance was still there, so star → owner spends →
   unstar → star again printed currency with a single account.
3. Why not fix the unstar edge instead? Any free action that creates
   currency is farmable with throwaway accounts; patching one loop leaves
   the class of bug alive.
4. Why keep the star counter at all? project.stars is reputation —
   ranking, discovery, bragging rights. Reputation may be cheap;
   currency may not.
5. Why is the fix safe for creators? Real income still flows through
   trades (buyer pays star_cost) and challenge bounties — both scarce,
   both ledgered.
"""
from django.db import IntegrityError, transaction
from django.db.models import F, Sum

from users.models import Profile, StarEvent

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

    5 Whys (verified email gate):
    1. Why require email_verified to trade? The seller is paid real,
       spendable stars. An unverified account is free to script.
    2. Why gate the trade, not the download? Free downloads harm nobody;
       only the currency transfer needs a scarce counterparty.
    3. Why not gate on account age? Age is free too — it only slows the
       farm down. A mailbox is the cheapest real cost we can demand.
    4. Why is the welcome grant on the same gate? One rule — currency
       enters and moves only for verified accounts — is auditable.
    5. Why check inside this function, not the view? Every future caller
       (API, admin tool) must hit the same wall.
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

    try:
        if not buyer.profile.email_verified:
            raise TradeError(
                'Confirm your email before trading stars — check your inbox '
                'for the verification link (Settings → resend).'
            )
    except Profile.DoesNotExist as exc:
        raise TradeError('Account profile is missing. Refresh and try again.') from exc

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
                    'Earn stars by publishing vibes that get traded.'
                )
            Profile.objects.filter(pk=buyer_p.pk).update(stars_balance=F('stars_balance') - cost)
            Profile.objects.filter(pk=seller_p.pk).update(stars_balance=F('stars_balance') + cost)
            trade = Trade.objects.create(
                buyer=buyer,
                seller=project.owner,
                project=project,
                cost=cost,
            )
            # Ledger both sides INSIDE the same transaction as the balance
            # moves — a crash cannot leave money the ledger can't explain.
            StarEvent.objects.create(
                user=buyer, delta=-cost, reason='trade_spend',
                ref=f'trade:{trade.pk}:{project.slug}',
            )
            StarEvent.objects.create(
                user=project.owner, delta=cost, reason='trade_earn',
                ref=f'trade:{trade.pk}:{project.slug}',
            )
            return trade
    except IntegrityError:
        existing = Trade.objects.filter(buyer=buyer, project=project).first()
        if existing:
            return existing
        raise TradeError('Could not complete the trade. Try again.')


def toggle_project_star(user, project):
    """Atomically star or unstar. Returns True if the vibe is now starred.

    Reputation only — the wallet is untouched.

    5 Whys:
    1. Why lock the project row? Two tabs both get_or_create, then the loser
       treats it as an unstar and deletes the winner's Star.
    2. Why select_for_update on Star too? So the delete and the counter move
       cannot interleave with another toggle.
    3. Why IntegrityError on create? Unique (user, project) is the real lock
       if two creates sneak in before the row lock is held.
    4. Why not let stars go negative? Unstar uses stars__gt=0, same as the
       existing floor test.
    5. Why does the owner's wallet NOT move here? Starring is free and
       reversible; paying spendable currency for it was a minting loop
       (star → spend → unstar → star). See module docstring.
    """
    from .models import AppProject, Star

    with transaction.atomic():
        locked = AppProject.objects.select_for_update().get(pk=project.pk)
        existing = Star.objects.select_for_update().filter(user=user, project=locked).first()
        if existing:
            existing.delete()
            AppProject.objects.filter(pk=locked.pk, stars__gt=0).update(stars=F('stars') - 1)
            return False
        try:
            Star.objects.create(user=user, project=locked)
        except IntegrityError:
            return True
        AppProject.objects.filter(pk=locked.pk).update(stars=F('stars') + 1)
        return True


def stars_earned(user) -> int:
    return Trade.objects.filter(seller=user).aggregate(total=Sum('cost'))['total'] or 0


def stars_spent(user) -> int:
    return Trade.objects.filter(buyer=user).aggregate(total=Sum('cost'))['total'] or 0
