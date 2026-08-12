# Weekly Season & Rewards — Spec

**Why weekly reset?** All-time leaderboard is static — #1 never changes. Weekly gives newbies a chance, creates urgency.

**Why season not just reset stars?** Stars are permanent reputation, but weekly wins are per season. At Monday 00:00, new season starts, wins reset to 0, stars stay.

**How:**
- Season model: {number, start, end, is_active}
- Battle leaderboard has tabs: All Time (stars) vs This Week (wins in last 7 days)
- Reward for #1 of week: +10 ★, Pro 7 days, badge “Week 12 Champion” on profile, appears in feed banner.
- Auto-reset via Celery beat every Monday, or manual `Season.objects.create()`.

**Backend:** `VibeBattle.created_at` and `BattleVote.created_at` already have timestamps. Weekly query: `VibeBattle.objects.filter(created_at__gte=monday)`.
