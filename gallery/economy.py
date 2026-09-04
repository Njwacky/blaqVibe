"""Stars economy — the working money path.
No external API. Buyer spends star_cost, seller receives the same amount,
a Trade row is the receipt, a pair of StarEvent rows is the ledger, and
that Trade unlocks the ZIP.
"""
from django.db import IntegrityError, transaction
from django.db.models import F, Sum

from users.models import Profile, StarEvent

from .access import effective_star_cost
from .models import AppProject, Trade

class TradeError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)

def split_shares(project, total):
    """Distribute `total` stars among owner + co-owners.
        Returns [(user, amount), ...] where amounts sum EXACTLY to total and
        every amount is >= 1 (zero-share recipients are omitted, so the list
        may be shorter than the team). Owner keeps 100 − Σ(co-owner shares).
    """
    co = list(project.co_owners.select_related('user').order_by('id'))
    used = sum(c.share_percent for c in co)
    if used > 100:
        # Defensive only — the app never allows this state.
        return [(project.owner, total)]
    entries = [(project.owner, 100 - used)]
    entries += [(c.user, c.share_percent) for c in co]
    entries = [(u, p) for u, p in entries if p > 0]
    if not entries or total <= 0:
        return []

    quotas = []
    for order, (u, p) in enumerate(entries):
        quotas.append((u, total * p // 100, total * p % 100, order))
    base = {}
    given = 0
    for u, q, _r, _o in quotas:
        base[u] = q
        given += q
    remainder = total - given
    # Largest fractional remainder first; ties break by stable order.
    for u, _q, r, order in sorted(quotas, key=lambda t: (-t[2], t[3])):
        if remainder <= 0:
            break
        base[u] += 1
        remainder -= 1
    return [(u, amt) for u, amt in base.items() if amt > 0]

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
            # Lock the PROJECT row first: co-owner shares can change while a
            # trade is mid-flight. Locking serializes "edit the split" vs
            # "pay out" so the distribution always matches the current team.
            locked = AppProject.objects.select_for_update().get(pk=project.pk)
            # Re-check under the lock. The old schema-level unique
            # (buyer, project) is gone (co-owner splits need one Trade row
            # per recipient), so the project row is now the serialization
            # point: a concurrent first-purchase waits on this lock, sees
            # the rows we insert, and returns them instead of paying twice.
            existing_locked = Trade.objects.filter(buyer=buyer, project=locked).first()
            if existing_locked:
                return existing_locked
            try:
                buyer_p = Profile.objects.select_for_update().get(user=buyer)
            except Profile.DoesNotExist as exc:
                raise TradeError('Account profile is missing. Refresh and try again.') from exc
            if buyer_p.stars_balance < cost:
                raise TradeError(
                    f'Need {cost} ★ to trade for “{project.title}” — you have {buyer_p.stars_balance} ★. '
                    'Earn stars by publishing vibes that get traded.'
                )
            # One Trade + ledger row PER RECIPIENT (owner + each co-owner)
            # rather than one row with multiple sellers: Trade.seller is a
            # single FK used by ranks, payout dashboard, trading history and
            # ledger refs. Per-recipient rows keep every existing query correct
            # unchanged — each person's "sold" list shows exactly their share.
            # Lock order follows split_shares (owner first, then co-owners by
            # id) so concurrent trades never deadlock.
            shares = split_shares(locked, cost)
            if not shares:
                raise TradeError('No recipient for this trade. Try again.')
            trades = []
            for recipient, share in shares:
                recipient_p = Profile.objects.select_for_update().get(user=recipient)
                Profile.objects.filter(pk=recipient_p.pk).update(stars_balance=F('stars_balance') + share)
                t = Trade.objects.create(
                    buyer=buyer,
                    seller=recipient,
                    project=locked,
                    cost=share,
                )
                trades.append(t)
                # Ledger INSIDE the same transaction as the balance moves —
                # a crash cannot leave money the ledger can't explain.
                StarEvent.objects.create(
                    user=recipient, delta=share, reason='trade_earn',
                    ref=f'trade:{t.pk}:{locked.slug}',
                )
            Profile.objects.filter(pk=buyer_p.pk).update(stars_balance=F('stars_balance') - cost)
            StarEvent.objects.create(
                user=buyer, delta=-cost, reason='trade_spend',
                ref=f'trade:{trades[0].pk}:{locked.slug}',
            )
            return trades[0]
    except IntegrityError:
        existing = Trade.objects.filter(buyer=buyer, project=project).first()
        if existing:
            return existing
        raise TradeError('Could not complete the trade. Try again.')

def toggle_project_star(user, project):
    """Atomically star or unstar. Returns True if the vibe is now starred.
        Reputation only — the wallet is untouched.
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
