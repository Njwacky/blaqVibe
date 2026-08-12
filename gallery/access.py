"""Download / file / git access — backend only.

A vibe is free when it has no star cost and no ZAR price (or the
owner turned trading off). Otherwise the buyer needs a Trade, a Sale,
or to be the owner / staff.
"""
from .models import Sale, Trade


def effective_star_cost(project) -> int:
    try:
        if not project.owner.profile.allow_trading:
            return 0
    except Exception:
        pass
    return int(project.star_cost or 0)


def effective_price_zar(project) -> int:
    return int(project.price_zar or 0)


def user_can_download(user, project) -> bool:
    if getattr(project, "status", None) != "published":
        return False
    if not getattr(project, "zip_file", None):
        return False
    if getattr(user, "is_authenticated", False):
        if user.pk == project.owner_id or getattr(user, "is_staff", False):
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
