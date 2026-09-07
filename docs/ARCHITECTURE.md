# BlaqVibes Architecture — as shipped

> **Source of truth.** This document records the product architecture that is
> implemented on `master` (delivered via PR #74, then extended with the
> navigation (§3a), Discover/Build (§4a/§4b), skill versions (§5a),
> provenance (§7a), sales metrics (§8a) and profiles (§8b) sections below.
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

## 3a. Navigation is five destinations

**PROJECTS | DISCOVER | SKILLS | CHALLENGES | BUILD** — nothing else may sit
in the primary nav. Saved, Inbox, Battle, Launch guides, Nolo, Trades, Sales,
Settings, Moderation and Admin live in the account menu (or the footer for
signed-out visitors). The rule is enforced by a test that reads the rendered
`<nav>` and fails when a utility URL appears in it.

Canonical URLs: `/` (projects feed), `/discover/`, `/skills/`, `/challenges/`,
`/build/`. `/prompt-skills/…` 301-redirects to `/skills/…` — the prompt-era
prefix is dead, the links are not.

## 4. Remix is the signature feature

- `/app/<slug>/forks/` renders the real **family tree** (BFS over forks):
  total remixes, generations deep, builders involved. Entering from any
  descendant resolves the whole tree; unpublished forks stay invisible to
  strangers.
- Feed cards show `⑂ N` published remixes; the “Fresh remixes” rail tells
  “@remixer remixed \<original\> by @origin” with both sides linked.

## 4a. DISCOVER — the remix half of the loop, as data

`gallery/remix_stats.py` derives everything from one column (`forked_from`)
and one status filter (`published`):

- originals vs remixes vs deepest generation (`remix_totals`),
- **most remixed** projects,
- **fastest-growing families**, keyed by the *original* project so a
  remix-of-a-remix credits the idea it descends from,
- **top remixers** — builders who remix *other people's* work (remixing your
  own does not count),
- fresh remixes and remixable candidates for the Build page.

Unpublished remixes never appear in any of it, so a leaderboard cannot leak
what the project page hides. Every function is crush-safe: a broken rail is
an empty state, never a 500.

## 4b. BUILD is a workflow, not a button

`/build/` offers three honest entry points — **start from scratch**,
**remix a project**, **use a Builder Skill** — then shows the same six steps
(CREATE → UPLOAD → SHOW → FEEDBACK → IMPROVE → PUBLISH). Using a skill
redirects into `/build/?skill=…` and the page keeps showing that open loop
until a published project claims it.

## 5. Builder Skills (SKILL → BUILD → PROJECT → PROOF)

- A skill is knowledge a builder shares; using one records a `SkillUse`.
- An unclaimed use attaches to the next project the same builder publishes
  within 2 h, and the project page credits it: *“Built using Builder Skill …”*
  — the project is the skill's proof. Skills show “used by N builders ·
  produced M published projects”.

## 5a. Skill versions are immutable (SKILL → SKILL VERSION → USE → …)

A `Skill` is the living, editable page. Every published state of it is frozen
into a `SkillVersion` row that **refuses to be saved twice** — editing a skill
publishes the next version instead of rewriting the last one. An edit that
changes nothing does not inflate the number.

`SkillUse` records the version a builder started from, and the attachment
bridge writes `AppProject.source_skill` + `source_skill_version` onto the
project itself (§17/§18). The project page therefore says *"Built using
Builder Skill X (v1)"* and keeps saying v1 after the author rewrites the
workflow — that is what makes it evidence rather than a label.

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

## 7a. Project provenance columns (§17/§18)

A project knows: `owner` (creator), `forked_from` (source project),
`source_skill`, `source_skill_version`, derived `build_method`,
`ProjectEvent` history, and `published_at` — the first time it became
public, written by the platform and never moved again (a re-queue →
re-publish cycle is a new *version* row, not a new birthday). Migration
`0037` backfills all of it from existing rows; nothing is invented.

## 8. Sales clarity

The buy box answers, before any money moves: *what you get* (file count in
the ZIP), *safety status*, *preview availability*, *who built it*, *usage
terms* (README carries the creator's license; personal use by default), and
*how payment works* (buyer → Paystack → verified charge → unlock). Owners see
sales on the **Sales** page; there is no payout concept anywhere.

## 8a. Sales metrics are activity (§11)

The Sales page reports **Projects sold · Unlocks (card/star) · Views ·
Conversion rate · Purchases · Popular projects** — buyer activity, never a
balance. Conversion is withheld below 20 views because "1 of 3" is noise,
not a rate. The banned vocabulary (earnings, cash out, payout, creator
balance) is pinned by tests.

## 8b. Profiles are about builders (§13)

Tabs: **Projects · Remixes · Skills · Activity · Reputation**, then the
social lists. Remixes show lineage and how often *others* remixed this
builder; Skills shows what they published and what they built with; Activity
is platform-written `ProjectEvent` history filtered by visibility;
Reputation collects rank/stars/originals/remixed-by-others and says plainly
that points are not the reason to be here. `?tab=vibes` still lands on
Projects.

## 9. Gamification stays in the background

XP, levels and badges exist as server-side behaviour and on opt-in surfaces
(challenges, own progress). Public copy never leads with XP — projects and
people first, points quietly behind.

## 10. Engineering guardrails

- Every feature ships with tests; the suite is the contract
  (855 tests green at delivery).
- Regression guards: no literal `{{`/`{%`/`{#` may ever render — Django's
  `{# #}` cannot span lines, so multi-line notes use
  `{% comment %}`/`{% endcomment %}` and the project page is asserted clean;
  per-user cache isolation for the pulse loop; honest empty states everywhere
  (no fake bounties, no fake activity).
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
