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

    5 Whys:
    1. Why largest-remainder instead of truncation? 5★ split 34/33/33:
       floor gives 1+1+1 = 3 — two stars would vanish (or be minted if we
       rounded up). Largest remainder hands the leftover to the biggest
       fractional parts, so Σ == total always: buyer pays N, team gets N,
       the ledger reconciles. A star is never created or destroyed by
       arithmetic.
    2. Why integer math (`p // 100`, `p % 100`) instead of floats?
       Float division is fine at 2 decimals but the remainder loop needs
       exactness; integer quotients/remainders are exact by construction.
    3. Why omit zero-share recipients instead of creating 0★ Trade rows?
       A "+0 ★" ledger row and Trade row is noise that breaks the "you
       earned" display; the remainder pass guarantees the omitted rows
       truly would have been 0.
    4. Why owner first, then co-owners by id? The returned order is the
       lock order in trade_for_download; a stable total order across
       concurrent trades prevents deadlocks.
    5. Why fall back to owner-gets-all when shares exceed 100? That state
       is unreachable through the app (form validates Σ ≤ 100), but a
       direct DB write must not pay out more than 100% — fail safe.
    """
    co = list(project.co_owners.select_related('user').order_by('id'))
    used = sum(c.share_percent for c in co)
    if used > 100:
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
            locked = AppProject.objects.select_for_update().get(pk=project.pk)
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
