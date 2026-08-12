# 5 Whys: Why stars → rank? Incentive to publish quality. Why bonus for trending? High stars = appears 1st.
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
    # Total stars earned from vibes (owner's vibes stars sum + trades earned)
    # For now: sum of vibes stars + stars field on profile (if we store)
    try:
        total = sum(p.stars for p in user.projects.all())
    except Exception:
        total = 0
    # Also add Trade earnings if exists
    try:
        from .models import Trade
        earned = Trade.objects.filter(seller=user).count() * 2  # 2 per trade
        total += earned
    except Exception: pass
    return get_rank(total)
