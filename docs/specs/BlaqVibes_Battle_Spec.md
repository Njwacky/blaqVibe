# Vibe Battles — Spec (5 Whys, No Shortcuts)

**Feature:** Two random vibes side-by-side → “Which would you pick?” → vote → winner +5 ★ + trending boost → appears top.

## 5 Whys

1. Why battle not just star? Star is like, battle is *choice* — forces comparison, fun, like Tinder. New users get stars without coding.
2. Why +5 ★ not +1? Battle win is worth more than a star, so winner jumps to top 1st quickly.
3. Why random pair? Discovery — you see vibes you’d never search for.
4. Why backend not JS? Vote must be atomic `F('stars')+5`, prevent double vote via `BattleVote(user, battle)`.
5. Why at scale? 10k vibes → battles are endless content, no curation needed.

## Model

- `VibeBattle(vibe_a, vibe_b, votes_a, votes_b, created_at)` — one battle = 2 vibes
- `BattleVote(user, battle, choice='a'|'b', created_at)` — unique_together, prevents double vote

## Flow

1. GET `/battle/` → pick 2 random published vibes (exclude own, not same, preferably same category or random) → create `VibeBattle` or reuse recent → show side-by-side cards with preview, README snippet, stars, languages.
2. User clicks **Pick Left / Pick Right** → `POST /battle/<id>/vote/` → `choice` → `BattleVote` + `VibeBattle.votes_a/b +=1` + `winner.stars +=5` (atomic) + `winner.forks` bonus for trending.
3. Next battle auto-loads (or Show Next → new pair).
4. Feed `sort=trending` includes battle wins: `score = clones*3 + stars + forks*2 + battle_wins*5`.

## UI

- Side-by-side, VS badge in middle, Pick buttons, progress bar, “Next Battle” after vote.
- No JS secrets — vote is POST with CSRF, backend checks not voted before.

## Security

- Rate limit 20 battles/hour per user, crush silently.
- Cannot vote on own vibe.
