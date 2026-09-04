RANKS = [
    (0, 'Bronze', 0, 0),
    (10, 'Silver', 10, 5),
    (50, 'Gold', 25, 15),
    (200, 'Platinum', 50, 30),
]

def get_rank(total_stars):
    rank = RANKS[0]
    for threshold, name, discount, bonus in RANKS:
        if total_stars >= threshold:
            rank = (threshold, name, discount, bonus)
    return {'threshold': rank[0], 'name': rank[1], 'discount': rank[2], 'bonus': rank[3]}

def contributor_bonus(user):
    cached = getattr(user, '_rank_cache', None)
    if cached is not None:
        return cached
    from django.db.models import Sum
    try:
        total = user.projects.aggregate(s=Sum('stars'))['s'] or 0
    except Exception:
        total = 0
    try:
        from .economy import stars_earned
        total += stars_earned(user)
    except Exception:
        pass
    rank = get_rank(total)
    try:
        setattr(user, '_rank_cache', rank)
    except Exception:
        pass
    return rank
