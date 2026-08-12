# Behind nolo.py — No Judgment, Just Facts

**File:** `gallery/nolo.py` (42 lines, backend only, no JS, no LLM key)

### Why Nolo exists (5 Whys)
1. Why compare? Buyer sees 2 “stock” vibes, both say “portfolio”. Needs to know *which function* differs.
2. Why not let Nolo judge “A is better”? That biases trade. Nolo must be neutral, like a spec sheet.
3. Why backend not JS? JS could be tampered to fake comparison. Backend extraction is truth from DB.
4. Why not LLM? LLM hallucinates and needs API key + leaks secrets. Deterministic keyword extraction is fast, offline, 10ms.
5. Why at scale? 10k vibes → need instant “diff” without AI cost.

### How it works — line by line

**`extract_features(project)`**
- Reads `readme.lower() + tech_stack.lower()` — the spec.
- Checks 13 keywords (chart, auth, api, tailwind, react, django, stripe, realtime, websocket, csv, tradingview, twelve, table, dashboard). If keyword in text → adds label.
- Returns sorted unique list, e.g., `['Chart','Django','Table']`. No AI, no network.

**`compare_apps(a,b)`**
- Builds `info(p)` for each: title, slug, tech_stack, languages (from `language_stats` — already size-weighted), file_count, stars, clones, features (from above), readme_len.
- Computes `diff`: `only_in_a` = features in A not B, `only_in_b` = opposite, `common` = intersection.
- Returns `{'a': {...}, 'b': {...}, 'diff': {...}}` — JSON, no judgment sentence. Frontend renders side-by-side.

### What Nolo does NOT do
- No `if a.stars > b.stars: recommend A` — that’s judgment. User decides.
- No raw `scan_report` or secrets — backend only returns sanitized features.
- No JS — all in `gallery/nolo.py`, called via `POST /nolo/compare/` (CSRF, login required, rate-limited in future).

### Future (no shortcuts)
- Replace keyword dict with TF-IDF on README → auto-extract top 10 terms.
- Add `file_tree` feature: “A has `frontend/src/TradingView.jsx`, B has `stock_app/views.py`”.

### Security
- No `eval`, no `exec`, no external API. Input is `a_slug, b_slug` validated via `get_object_or_404` + status check. No XSS because result is JSON, not HTML.
