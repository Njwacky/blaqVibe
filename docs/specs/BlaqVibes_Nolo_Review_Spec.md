# BlaqVibes — Nolo + Reviews (Spec, 5 Whys)

**User said: Nono and reviews → Nolo and Reviews**

## Nolo Auto-Review (AI on upload)

**5 Whys:**
1. Why auto-review? Queue checks virus, not quality. New users get 0 stars and don't know why.
2. Why Nolo? Nolo already compares, now she grades: score 0-10 + 3 fixes.
3. Why backend LLM? Prompt = readme + file_tree + language_stats → LLM returns JSON, stored in scan_report.review, never in JS.
4. Why not block publish? Review is advisory, not gate. Shows as “Nolo says: 7/10 — Add requirements.txt, add README heading”.
5. Why at scale? 10k uploads → instant feedback without human moderator.

**How:** `gallery/nolo_review.py:nolo_review(project)` → if OPENAI_API_KEY set, call LLM, else heuristic (checks: has README heading, has requirements.txt, has file_count >5, has language %). Saves to `project.scan_report['nolo_review'] = {score, fixes, pros}`.

**When:** Called in `tasks.vulnerability_scan` chain after virus scan, before finalize.

## Reviews (Human, 1-5 ★ + text, separate from Comment/Star)

**5 Whys:**
1. Why reviews not just comments? Comment = question, Review = rating + verdict for trading decision.
2. Why 1-5? Stars already 1, but review aggregates to avg rating (e.g., 4.3 ★ (12 reviews)) — like App Store.
3. Why one review per user per vibe? Prevent spam, allow edit.
4. Why backend? Rating affects trending + rank, must be atomic.
5. Why at scale? Reviews drive trading — high-rated appears top, like Amazon.

**Model:** `Review(project, user, rating 1-5, text 10-1000, created_at)` unique_together, `project.avg_rating` cached (denormalized on save).

**Flow:** Detail page → Reviews tab (avg 4.3 + count + histogram) → form (if bought/traded or starred, can review) → POST → recalc avg.

**Nolo vs Human:** Nolo is AI auto-review (backend, on every upload, score), Reviews are human (1-5 + text). Both show in Reviews tab, Nolo badge “🤖 Nolo”.
