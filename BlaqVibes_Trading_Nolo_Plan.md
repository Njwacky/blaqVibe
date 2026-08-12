# BlaqVibes — Trading Economy + Language % + Nolo Compare (Plan)

**User Quote:** “If someone wants to download your code, you don’t just download — you need to trade: ranks but we use stars. Some apps cost 2 bronze etc. High stars → top 1st when exploring → good contributor → discount on Pro. New users must build good apps to earn stars. People check your MD and compare, or ask Nolo to compare (Nolo doesn’t judge, just compares).”

## 1. Language % — 5 Whys
1. Why %? Buyers judge stack before trade (“70% Python, 20% JS”). 2. Why not just tech_stack field? Auto-detect from ZIP extensions is truth. 3. Why backend? Client could lie. 4. Why donut not just list? Visual at glance. 5. Why at scale? Search filter “Python >50%”.

## 2. Trading with Stars — 5 Whys
1. Why not free download? Free = leech, no incentive to publish quality. 2. Why stars as currency? Stars already = reputation, now = currency (earn by publishing, spend to download). 3. Why bronze/silver/gold? Gamify ranks. 4. Why cost per app? Creator sets price (0-5 stars, default 0 for snippets). 5. Why top rank boost? High stars = social proof → appears 1st on Feed `sort=trending` → more clones → more stars → discount.

Economy:
- New user: 5 starter stars (can download 2-3 cheap apps)
- Earn: +1 star when someone stars your vibe, +2 when someone trades (downloads) your vibe
- Spend: Download costs `app.star_cost` (0=free snippet, 1=bronze, 3=silver, 5=gold)
- Trade: `Trade(buyer, seller, app, cost)` → buyer.stars -= cost, seller.stars += cost (or + contributor_points)
- Ranks (from total earned stars):
  Bronze 0-9, Silver 10-49, Gold 50-199, Platinum 200+ → `trending` boost: score = clones*3 + stars + rank_bonus (Bronze 0, Silver 5, Gold 15, Platinum 30)
- Discount: Rank → Pro plan discount: Bronze 0%, Silver 10%, Gold 25%, Platinum 50%

## 3. Nolo Compare — 5 Whys
1. Why compare? Buyer has 2 “stock tracker” vibes, needs function compare. 2. Why Nolo not judge? Avoid bias, Nolo just extracts facts side-by-side. 3. Why backend logic? AI prompt + feature extraction stays backend. 4. Why MD check? README is spec — Nolo parses tech_stack, features, file count. 5. Why then user chooses? User picks function he wants, not AI.

Nolo endpoint: POST /nolo/compare/ {a_slug, b_slug} → backend extracts: tech_stack, language %, file_count, readme bullets, stars, rank. Returns JSON side-by-side + “Differences” (e.g., A has Chart.js, B has TradingView). No judgment sentence.

## 4. Implementation (No Shortcuts, Full Code)
- `gallery/language.py: detect_languages(zip_path) -> {'Python': 68, 'JavaScript': 22, 'CSS': 10}` via ext map + file size weighting.
- `AppProject.language_stats JSONField + star_cost IntegerField (0-5) + rank_bonus cached`
- `gallery/models.py: Trade(buyer, seller, app, cost, created_at)`
- `gallery/views.py: trade_download()` — checks buyer stars, creates Trade, updates stars atomically (F()), then presigned URL.
- `gallery/ranks.py: get_rank(total_stars) -> rank, discount, bonus`
- `gallery/nolo.py: compare_apps(a,b) -> {a:{},b:{},diff:[]}` backend only.
- Templates: Detail shows donut (CSS conic-gradient) + cost badge “2 ★ Bronze” + Trade button + rank badge on owner avatar.
