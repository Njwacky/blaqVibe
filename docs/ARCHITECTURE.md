# BlaqVibes Architecture — as shipped

> **Source of truth.** This document records the product architecture that is
> implemented on `master` (delivered via PR #74, commits `e1e2634` → `a1e5d6b`).
> Where older files under `docs/specs/` disagree with this document, they are
> **historical** and must not be revived without an explicit decision.
> In particular: no payout/cash-out identity, no AI-prompt identity.

The loop we sell: **BUILD → SHOW → REMIX → COMPETE.**
The home answers one question first: **“What are people building?”**

## 1. Identity

- Community-first. Landing, feed and share previews lead with *projects and
  people*, not with the visitor's own dashboard.
- No AI-prompt identity. Builders may build by hand, with AI as a tool, or by
  remixing — the *build method* is evidence shown on the project, never the
  platform's identity.
- No payout/earnings vocabulary. Money surfaces are **Sales**, **Buyer
  Activity**, **Project Sales**, **Purchases**. Historical payout models and
  migrations were kept (data safety) but are dead to the UI.

## 2. Trust as architecture

- Every upload passes the scan pipeline (viruses, secrets, dependency audits).
- Trust grades: `verified` / `scanned` / `unknown`, explained at `/trust/`.
- The feed offers a **🛡️ Checked only** filter that is honest: nothing
  verified → empty grid, never a quiet refill.
- Buyer clarity always shows the project's safety status before payment.

## 3. The project page is the heart

Story header → evidence grid (preview, files, README, trust) → remix lineage
→ history timeline → actions (**⑂ Remix**, **⧉ Share**, **BUY & UNLOCK**).
A visitor can judge *what it does, how it was built, whether it is safe, and
how far the idea travelled* without leaving the page.

## 4. Remix is the signature feature

- `/app/<slug>/forks/` renders the real **family tree** (BFS over forks):
  total remixes, generations deep, builders involved. Entering from any
  descendant resolves the whole tree; unpublished forks stay invisible to
  strangers.
- Feed cards show `⑂ N` published remixes; the “Fresh remixes” rail tells
  “@remixer remixed \<original\> by @origin” with both sides linked.

## 5. Builder Skills (SKILL → BUILD → PROJECT → PROOF)

- A skill is knowledge a builder shares; using one records a `SkillUse`.
- An unclaimed use attaches to the next project the same builder publishes
  within 2 h, and the project page credits it: *“Built using Builder Skill …”*
  — the project is the skill's proof. Skills show “used by N builders ·
  produced M published projects”.

## 6. Discovery — a reason to come back tomorrow

- Landing rails (unfiltered feed only): trending/new, fresh remixes, rising
  creators, suggested creators, weekly activity line.
- Authenticated builders additionally get **BUILDER PULSE** (“What are people
  building?”): recently built across the network, worth-remixing from
  followed creators, your latest project, unread signal — cached *per user*,
  never leaking across users.
- Daily challenges give a concrete reason to return; the loop footer reads
  *Build → Show → Remix → Compete*.

## 7. Sharing markets the platform

- Project pages ship rich `og:`/`twitter:` meta: name, creator, what it does,
  build method, stars — plus the thumbnail when one exists.
- Without a thumbnail, `/app/<slug>/share-card.png` renders a 1200×630 story
  card server-side (PIL): name, creator, description, build-method chip,
  trust grade, stars, remixes, CTA. Glyph-safe, cached 1 h, 404 for
  unpublished slugs.
- Creator profiles carry their own share meta (published count, stars
  received, followers). The **⧉ Share** button copies the canonical link.

## 8. Sales clarity

The buy box answers, before any money moves: *what you get* (file count in
the ZIP), *safety status*, *preview availability*, *who built it*, *usage
terms* (README carries the creator's license; personal use by default), and
*how payment works* (buyer → Paystack → verified charge → unlock). Owners see
sales on the **Sales** page; there is no payout concept anywhere.

## 9. Gamification stays in the background

XP, levels and badges exist as server-side behaviour and on opt-in surfaces
(challenges, own progress). Public copy never leads with XP — projects and
people first, points quietly behind.

## 10. Engineering guardrails

- Every feature ships with tests; the suite is the contract
  (855 tests green at delivery).
- Regression guards: no literal `{{`/`{%` may ever render (template tags must
  not span lines); per-user cache isolation for the pulse loop; honest empty
  states everywhere (no fake bounties, no fake activity).
- Backend-only rendering for anything user-adjacent (share card draws
  sanitized fields as plain text).

## Superseded / historical specs (do not revive as-is)

- `docs/specs/BlaqVibes_Money_Architecture.md`,
  `docs/specs/BlaqVibes_Trading_Nolo_Plan.md` — pre-§1 money model; payout
  vocabulary removed (see §1, §8).
- `docs/specs/BlaqVibes_Prompt_Audit.md` and prompt-workbench era docs —
  pre-§5 identity; replaced by Builder Skills.
- `PRODUCT_RETENTION_AUDIT.md`, `LAUNCH_DESIGN_ANALYSIS.md` — inputs to this
  architecture, not the current truth.
