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
    if status == "quarantined":
        return False
    if not getattr(project, "zip_file", None):
        return False
    if getattr(user, "is_authenticated", False) and user.pk == project.owner_id:
        return True
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
