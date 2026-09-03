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

    This is deliberately fail-closed: an object with no usable status is not
    public. Callers that expose project-derived metadata should use this
    policy before rendering the object, not after a broad ``try/except``.

    'removed' (soft-deleted): the page is gone for everyone except
    moderators — buyers keep the *download*, not the listing. 5 Whys:
    Why hide the page from buyers too? The creator asked for the vibe to
    be gone; the purchase contract covers the ZIP, not the storefront.
    """
    if project is None:
        return False
    status = getattr(project, 'status', None)
    if status == 'published':
        return True
    if not getattr(user, 'is_authenticated', False):
        return False
    owner_id = getattr(project, 'owner_id', None)
    if owner_id is not None and user.pk == owner_id:
        return True
    return user_is_moderator(user)


def user_can_review_pr(user, pr) -> bool:
    """Return whether *user* may read a pull-request's source/diff.

    PR pages can expose source ZIP contents, so visibility is checked against
    both objects involved rather than relying on the numeric PR id or target
    slug. The target must itself be published. A pending source is reviewable
    only by the source owner, a moderator, or the published target owner.

    Keeping this rule beside ``user_can_see_project`` gives every PR endpoint
    one fail-closed policy to reuse instead of subtly different IDOR checks.
    """
    if pr is None:
        return False
    target = getattr(pr, 'target', None)
    source = getattr(pr, 'source', None)
    if target is None or source is None:
        return False
    if getattr(target, 'status', None) != 'published':
        return False
    if user_can_see_project(user, source):
        return True
    return (
        getattr(user, 'is_authenticated', False)
        and getattr(user, 'pk', None) == getattr(target, 'owner_id', None)
    )


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
    if status != "published":
        return False
    if not getattr(project, "zip_file", None):
        return False
    if getattr(user, "is_authenticated", False) and user.pk == project.owner_id:
        return True
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
