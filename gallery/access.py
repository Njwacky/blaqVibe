"""Download / file / git access — backend only.

A vibe is free when it has no star cost and no ZAR price (or the
owner turned trading off). Otherwise the buyer needs a Trade, a Sale,
or to be the owner.

Staff/moderator is not a free download. Review happens in the
moderation queue; Django is_staff is not a BlaqVibes role.
"""
from .models import Sale, Trade


def user_is_moderator(user) -> bool:
    if not getattr(user, 'is_authenticated', False):
        return False
    try:
        return user.profile.is_moderator()
    except Exception:
        return False


def user_can_see_project(user, project) -> bool:
    """Published is public. Pending/quarantined is owner or moderator only.

    'removed' (soft-deleted): the page is gone for everyone except
    moderators — buyers keep the *download*, not the listing. 5 Whys:
    Why hide the page from buyers too? The creator asked for the vibe to
    be gone; the purchase contract covers the ZIP, not the storefront.
    """
    status = getattr(project, 'status', None)
    if status == 'published':
        return True
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.pk == project.owner_id:
        return True
    return user_is_moderator(user)


def effective_star_cost(project) -> int:
    try:
        if not project.owner.profile.allow_trading:
            return 0
    except Exception:
        pass
    return int(project.star_cost or 0)


def effective_price_zar(project) -> int:
    # 5 Whys: a ZAR-only vibe must not stay locked when cards are off.
    # Why lock? price_zar > 0. Why can't they pay? no PAYSTACK_SECRET_KEY.
    # Why no star fallback? star_cost may be 0. Why is that a fake paywall?
    # Because we hide Buy and still refuse the ZIP. Treat ZAR as 0 until cards work.
    try:
        from .payments import paystack_enabled
        if not paystack_enabled():
            return 0
    except Exception:
        return 0
    return int(project.price_zar or 0)


def user_can_download(user, project) -> bool:
    status = getattr(project, "status", None)
    if status == "removed":
        # Soft-deleted vibe: only the owner and people who already PAID
        # keep the ZIP. No new unlocks, no free downloads — the listing
        # is gone; existing receipts (Trade/Sale) still honour access.
        if not getattr(project, "zip_file", None):
            return False
        if not getattr(user, "is_authenticated", False):
            return False
        if user.pk == project.owner_id:
            return True
        return (
            Trade.objects.filter(buyer=user, project=project).exists()
            or Sale.objects.filter(buyer=user, project=project).exists()
        )
    # Quarantined means the scanner found something: nobody downloads that,
    # not even a buyer. A receipt is a promise about *their* purchase, not a
    # licence to serve flagged bytes to anybody's machine.
    if status == "quarantined":
        return False
    if not getattr(project, "zip_file", None):
        return False
    if getattr(user, "is_authenticated", False) and user.pk == project.owner_id:
        return True
    # 5 Whys — why does a receipt outrank the rescan state?
    # 1. Why does this matter? An edit (or a `git push`) moves a vibe back
    #    to pending while it is re-scanned. Under the old rule a buyer's
    #    download broke for the whole rescan — and forever if the scanner
    #    was unavailable. They paid; the outage was not theirs.
    # 2. Why not just keep serving the pending bytes? Because those bytes
    #    have not been scanned yet. The download view therefore serves the
    #    last *scanned* version (see last_scanned_version), never the
    #    un-checked archive.
    # 3. Why check the receipt before the price? A free vibe that is
    #    mid-rescan has no receipt, so it correctly stays locked for a
    #    stranger; only paid access survives the pipeline.
    # 4. Why not allow moderators? Review happens in the moderation queue
    #    and on the detail page; a download is not a review tool.
    # 5. Why is 'removed' handled above and 'quarantined' refused here?
    #    Removal is the creator's choice, quarantine is the scanner's;
    #    the first must not punish buyers, the second must protect them.
    if status == "pending" and getattr(user, "is_authenticated", False):
        if (
            Trade.objects.filter(buyer=user, project=project).exists()
            or Sale.objects.filter(buyer=user, project=project).exists()
        ):
            return True
    if status != "published":
        return False
    cost = effective_star_cost(project)
    price = effective_price_zar(project)
    if cost == 0 and price == 0:
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    if cost > 0 and Trade.objects.filter(buyer=user, project=project).exists():
        return True
    if price > 0 and Sale.objects.filter(buyer=user, project=project).exists():
        return True
    return False


def last_scanned_version(project):
    """The most recent archived ZIP for a vibe that is mid-rescan.

    AppVersion rows are written when an edit replaces the ZIP and when a PR
    merge swaps it, so the newest row is the newest set of bytes the
    pipeline has already checked. Serving *that* to an existing buyer keeps
    their purchase alive during a rescan without ever handing out bytes the
    scanner has not seen.

    Returns None when there is nothing scanned yet (a first upload that is
    still in the queue) — in that case there is nothing safe to serve and
    the caller says so honestly.
    """
    try:
        return project.versions.order_by('-created_at').first()
    except Exception:
        return None


def access_denied_message(user, project) -> str:
    cost = effective_star_cost(project)
    price = effective_price_zar(project)
    if not getattr(user, "is_authenticated", False):
        if cost or price:
            parts = []
            if cost:
                parts.append(f"{cost} ★")
            if price:
                parts.append(f"R{price}")
            return f"Sign in to unlock this vibe ({' or '.join(parts)})."
        return "Sign in to download this vibe."
    parts = []
    if cost:
        parts.append(f"trade {cost} ★")
    if price:
        parts.append(f"buy for R{price}")
    if parts:
        return f"Unlock “{project.title}” first — {' or '.join(parts)}."
    return "You don't have access to this download."
