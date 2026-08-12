# Pro Plan — Who Viewed + AI README + Money (Spec, 5 Whys)

**User wants:** 1) Pro can see who viewed your vibe 2) AI README 3) Money function

## 1. Pro — Who Viewed Your Vibe

**5 Whys:**
1. Why who viewed? Creator needs to know who is interested to follow up (like LinkedIn).
2. Why Pro only? High value, privacy — free users see count, Pro sees names.
3. Why not just views count? Count is anonymous, Pro sees `username + time + how many times`.
4. Why backend? View is `user -> vibe` with IP, must be stored, never in JS.
5. Why at scale? 10k views/day → need `VibeView` table with index + privacy (only Pro sees).

**How:** `VibeView(user, vibe, viewed_at, count)` — on `app_detail` if `request.user.is_authenticated`, `get_or_create` + `F('count')+1`. Pro check: `if vibe.owner.profile.is_pro` then show list, else show “Upgrade to Pro to see who viewed”.

## 2. AI README

**5 Whys:**
1. Why AI README? Vibe coders hate writing README — AI does it from file_tree + code.
2. Why not just Nolo review? Nolo scores, AI README writes the actual markdown.
3. Why Gemini free? Same key, backend, prompt `file_tree + tech_stack + code snippet` → `markdown README`.
4. Why not overwrite? If README exists, AI suggests, user clicks “Use AI README”.
5. Why backend? Prompt is code, never in JS.

**How:** `POST /app/<slug>/ai-readme/` → backend calls `ai_readme.py:generate_readme(vibe)` → Gemini → returns markdown → preview → user clicks “Apply” → `vibe.readme = ai_readme`.

## 3. Money — Real Money via Stars + Paystack

**5 Whys:**
1. Why money? Stars are virtual, real money sustains creators.
2. Why not just stars? Stars → Pro discount, but money → creator payout (Rands).
3. Why Paystack? ZA, ZAR, EFT, cards — perfect for Durban.
4. Why not just Pro subscription? Also allow `price_zar` per vibe: `0=free, R50, R150` — buyer pays via Paystack, creator gets 85% (BlaqVibes 15% fee), logged in `Sale`.
5. Why backend? Paystack webhook verifies payment, no JS secret.

**How:** `AppProject.price_zar` (0=free), `Sale(buyer, seller, vibe, amount_zar, paystack_ref)`, `POST /app/<slug>/buy/` → redirect to Paystack checkout → webhook `POST /paystack/webhook/` → verify → create Sale + grant download + star bonus.
