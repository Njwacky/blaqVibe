# BLAQVIBES — Product Standard (5 Whys)

> **Living contract.** Every feature ships only if it strengthens this model.
> Last audited against the codebase: 2026-09-15.

---

## The standard (non-negotiable)

BlaqVibes is **not**:

> A nice app with profiles, projects and a social feed.

That bar is too low. The bar is:

> **Can BlaqVibes solve a real problem significantly better than the tools people already use?**

Every feature must answer at least one:

| Filter question | Layer |
|---|---|
| Does this help someone **BUILD**? | 1 |
| Does this help someone **PROVE**? | 2 |
| Does this help someone surface **CAPABILITY** from work? | 3 |
| Does this help someone **DISCOVER** capability (not popularity)? | 4 |
| Does this help someone connect to an **OPPORTUNITY**? | 5 |

If the answer is **none of those** → **do not build it yet**.

---

## The four product questions

1. Why would a builder join?
2. Why would they keep using it?
3. Why would another person care about their projects?
4. Why would a company, client or collaborator trust what they see?

If we cannot answer those, we should not ship the feature.

---

## The 5 Whys (compressed)

### WHY 1 — Why is capability hard to judge?

Because platforms communicate **identity and claims**, not evidence.

**Problem:** Skills are claims.  
**Fix:** The **project** is the primary evidence.  
**Rule:** A capability should connect to something the person actually built.

### WHY 2 — Why aren't projects enough?

Because a project card is not automatically proof of contribution or quality.

**Problem:** A project is not automatically proof.  
**Fix:** A **Proof Layer** — identity, contribution, technology, evidence, activity, outcome.  
**Rule:** Never say "verified" without showing *why I should believe this*.

### WHY 3 — Why does proof matter?

People are not searching for project #7. They are searching for **capability**.

**Problem:** Projects are evidence; capabilities are the query.  
**Fix:** Derive:

```text
PROJECT → TECHNOLOGIES → CAPABILITIES → PERSON
```

**Rule:** "This person demonstrates X through these projects" beats a skill list.

### WHY 4 — Why does capability-based discovery matter?

Popularity ≠ capability. A talented builder with 150 followers can out-build someone with 10k.

**Problem:** Social feeds reproduce popularity bias.  
**Fix:** Discovery answers *"Who can do what I need?"* and **explains the match**.  
**Rule:** Not "Recommended developer."  
Instead: *"Matched because they built 7 Django projects, including 3 REST APIs."*

### WHY 5 — Why would anyone use BlaqVibes?

Not because it has profiles + projects + a feed. GitHub, LinkedIn, portfolios and social already exist.

**The stronger answer:**

> A platform where **what you build becomes evidence of what you can do**, and that evidence helps you get discovered for real opportunities.

```text
BUILD → DOCUMENT → PROVE → DEMONSTRATE CAPABILITY → GET DISCOVERED → FIND OPPORTUNITIES
```

---

## The deepest product

The product is not the profile.  
It is not even the project.

**The product is TRUST.**

> "Can this person actually do what they say they can do?"

BlaqVibes exists to reduce uncertainty around that question.

---

## The five layers

```text
        BUILD
          ↓
        PROVE
          ↓
      CAPABILITY
          ↓
       DISCOVER
          ↓
     OPPORTUNITY
          ↓
        BUILD   ← flywheel
```

### 1. BUILD

People create projects with substance:

- Title, description
- Problem / solution
- Features
- Technology
- Screenshots / demo / GitHub
- Architecture, challenges, results, contribution

### 2. PROVE

Evidence, not a hollow badge:

- GitHub / ZIP / file tree
- Live demo when real
- Screenshots / docs
- Development activity / history
- Contribution (what *they* did)
- Project status (completed, deployed, used)

### 3. CAPABILITY

Convert project evidence into demonstrated abilities.  
A tag with zero projects is a **claim** — do not show it as proof.

### 4. DISCOVER

Search by capability. Rank by demonstrated work. Explain the match.

### 5. OPPORTUNITY

Only after 1–4 work:

- Collaboration, freelance, jobs, teams, mentorship

Matched against **demonstrated capability**, not profile keywords.

---

## What we do **not** build first

| Do not start with | Why |
|---|---|
| Stories / Reels | Social costume, not proof |
| Complex feed ranking | Popularity trap |
| Groups / huge messaging | Not the core loop |
| Crypto / fake payouts | Trust poison |
| Full job marketplace day one | Opportunity without proof is LinkedIn |
| AI everywhere as identity | AI is a tool + receipt, not the product |
| Twenty profile sections | Claims without evidence |

These may earn a place **later**. They do not prove the core problem is solved.

---

## Version 1 scope (protect this)

1. **Profile** — basic identity  
2. **Projects** — central object  
3. **Build Detail** — strongest page in the product  
4. **Evidence** — GitHub/ZIP, demo, screenshots, technology, scan, history  
5. **Capabilities** — derived from projects only  
6. **Discovery** — search projects *and people* by capability, with match reasons  

That is enough to test the fundamental idea.

---

## Build Detail standard (hero page)

A high-quality Build Detail must make a stranger able to answer:

| Question | Required surface |
|---|---|
| What is it? | Title + clear description |
| What problem? | Problem statement |
| How solved? | Solution / README |
| What features? | Feature list (or detected + stated) |
| What tech? | Stack + language stats |
| Can I see it? | Preview **or** honest "no live preview" |
| Can I inspect code? | File tree / ZIP / git clone |
| Who built it / contribution? | Owner + human_did / co-owners |
| Is it still working / status? | Trust, history, ship readiness |
| What capabilities does this prove? | Explicit capability chips linked to person |

Ideal skeleton:

```text
------------------------------------------------
INVENTORY MANAGEMENT SYSTEM
Built by: @name
Python · Django · PostgreSQL
------------------------------------------------
THE PROBLEM
THE SOLUTION
FEATURES
TECHNOLOGY
PROOF (repo · demo · screens · docs · scan)
CONTRIBUTION (role + what they did)
CAPABILITIES DEMONSTRATED  → links to person
[ VIEW DEVELOPER ]
------------------------------------------------
```

---

## How we measure success (not vanity)

| Metric | Question it answers |
|---|---|
| **Project completion** | Do builds finish, not just start? |
| **Proof coverage** | % of projects with meaningful evidence |
| **Capability discovery** | Can someone find a person for a specific skill? |
| **Opportunity conversion** | Discovery → contact / collab / interview / work |
| **Trust** | After Build Detail, does confidence go up? |

Do **not** optimize primarily for: raw users, followers, likes, XP, notification volume.

---

## The hardest test

| Developer A | Developer B |
|---|---|
| 10,000 followers | 150 followers |
| Claims: Python, Django, AI, React | 6 completed projects |
| Little evidence | GitHub/ZIP, demos, explanations, contributions |

**If BlaqVibes works, B is easier to discover for relevant opportunities.**  
If popularity still dominates → core purpose failed.

---

## Concept scores (from the 5 Whys review)

| Lens | Score |
|---|---|
| Concept | **8.5 / 10** |
| If shipped as generic social/portfolio | **6 / 10** |
| Potential of Build → Proof → Capability → Opportunity | **9 / 10** |

The gap is execution. Protect at all costs:

> **BlaqVibes must make capability more believable.**

Lose that → another social feed → product direction failure.  
Nail it → a real reason to exist.

---

# CODEBASE AUDIT (2026-09-15)

Honest map of the five layers against this repository.

Legend: **✓ strong** · **~ partial** · **✗ missing / weak**

---

## Layer 1 — BUILD

| Requirement | Status | Where |
|---|---|---|
| Project as central object | ✓ | `gallery.models.AppProject` |
| Publish path (ZIP / snippet / GitHub import) | ✓ | `views.publish`, `repo_import`, `/import/github/` |
| Build hub (scratch / remix / skill) | ✓ | `build_views.build_hub`, `/build/` |
| Problem statement field | ✓ | `AppProject.problem_statement` |
| Tech stack + language detect | ✓ | `tech_stack`, `language_stats`, `language.py` |
| README (required + scaffold honesty) | ✓ | `proof.scaffold_readme`, `has_scaffold_readme` |
| Features list (structured) | ~ | Detected in Nolo (`nolo.extract_features`); **no first-class Features section on Build Detail** |
| Architecture / challenges / results fields | ~ | Mostly free-text README; no structured sections |
| Screenshots gallery | ~ | Single `thumbnail` only |
| External live demo URL | ✗ | No `demo_url` / external demo field — preview is in-app sandbox only |
| Linked external GitHub (beyond import) | ~ | Import exists; project does not always retain canonical GitHub URL as proof |

**Verdict:** Build is real. Structured *story* of the build (features, solution, challenges) is still thinner than the Build Detail standard.

---

## Layer 2 — PROVE

| Requirement | Status | Where |
|---|---|---|
| Files / ZIP / file tree | ✓ | `zip_file`, `file_tree`, `AppFile` |
| Sandboxed preview when honest | ✓ | `preview_mode`, `can_run_preview`, runner |
| Security scan + unfakeable trust | ✓ | `gallery.trust`, pipeline-written `trust` |
| Proof checks card | ✓ | `AppProject.proof_checks()`, Proof Card on `app_detail.html` |
| Human contribution stated | ✓ | `human_did` |
| AI as receipt, not costume | ✓ | `build_method`, `ai_tool`, `ai_prompt`, `ai_got_wrong` |
| Remix lineage + delta | ✓ | `forked_from`, `remix_changed`, fork network |
| Project history timeline | ✓ | `ProjectEvent` |
| Ship readiness checks | ✓ | `ship_readiness.py` |
| Strengthen-after-publish (proof later) | ✓ | `proof.strengthen_actions`, `/publish/done/` |
| Reviews as witnesses | ✓ | `Review` + `ran_it` / `readme_clear` |
| Activity / meaningful development | ~ | History + versions; no deep commit-activity proof for external GitHub |
| Outcome (deployed / used in production) | ~ | Launch guides exist; no structured "in production" outcome |

**Verdict:** Proof is one of the strongest parts of the product. Do not dilute it with fake "Verified!" badges.

---

## Layer 3 — CAPABILITY

| Requirement | Status | Where |
|---|---|---|
| Capabilities derived from published work only | ✓ | `ability.demonstrated_skills` — tags with zero projects are not invented as claims |
| Shown on profile | ✓ | `users/views.py` profile + `profile.html` |
| Proof CV | ✓ | `opportunity.proof_cv`, `/u/<user>/proof/` |
| AI maturity stages with evidence flags | ✓ | `ability.ai_maturity` |
| Explicit PROJECT → TECH → CAPABILITY → PERSON graph | ~ | Tech tokens counted; **no capability taxonomy** (e.g. "API Development" derived from stack+features) |
| Capability chips on Build Detail | ✓ | Proof card → CAPABILITIES DEMONSTRATED → links to `/capability/?q=` + profile |
| Builder Skills as teachable workflows with project proof | ✓ | `skill_models`, SKILL→VERSION→USE→BUILD→PROJECT |

**Verdict:** Capability exists as *counted stack tokens*, not yet as a first-class capability graph people can hire against.

---

## Layer 4 — DISCOVER

| Requirement | Status | Where |
|---|---|---|
| Project search (title/stack/readme) | ✓ | `search.search_projects`, feed `?q=` |
| Tech filter on feed | ✓ | `views.feed` `tech=` |
| Discover rails (remix, rising, skills) | ✓ | `build_views.discover` |
| People ranked by demonstrated capability | ✓ | `/capability/` via `gallery.capability.search_capability` |
| Match explanation ("why this person") | ✓ | “Matched because they built N projects using …” |
| Capability search landing (Django → projects then people) | ✓ | Projects pane + people pane; chips from published stacks |
| Popularity vs capability ranking | ✓ on `/capability/` | Ranked by project count + proof coverage — stars/followers ignored. Feed trending still uses appeal (social half). |

**Verdict:** Capability discovery is live at `/capability/`. Feed Discover remains remix-social; the hiring-style path is the Capability page.

---

## Layer 5 — OPPORTUNITY

| Requirement | Status | Where |
|---|---|---|
| No premature Jobs tab | ✓ | Explicit in `opportunity.py`: matching via Builds, no Jobs tab |
| Similar builds / open problems | ✓ | `similar_builds`, `open_problems`, `/problems/` |
| Proof CV shareable | ✓ | `proof_cv` |
| Contact / collab / hire conversion | ~ | Profile + tips; no structured opportunity match or request |
| Jobs / freelance marketplace | ✗ (correctly deferred) | Do not build until 1–4 convert |

**Verdict:** Opportunity is correctly treated as a **side effect of the object**. Keep it that way until capability discovery works.

---

## Parallel product identity (tension to manage)

| Existing loop (README / UX arch) | This standard |
|---|---|
| BUILD → SHOW → REMIX → COMPETE → REPEAT | BUILD → PROVE → CAPABILITY → DISCOVER → OPPORTUNITY |

These are **compatible** if:

- **SHOW** = Prove (evidence on the object)
- **REMIX** = social proof + lineage (not vanity)
- **COMPETE** never outranks capability discovery
- **OPPORTUNITY** stays downstream of proof

If COMPETE / feed / XP become the product, the 5 Whys standard fails.

Also retain the human wound from `docs/WHY_THEY_CHOOSE_BLAQVIBES.md`:

> The work is the identity. First publish must go live. Remix is the share button. Trust on the card.

That document is the **emotional** why. This document is the **capability-trust** why. Both must stay true.

---

## Gap priority (what to build next)

Ordered by leverage on the standard — not by shiny-ness.

### P0 — Protect the flywheel (must not regress)

1. Keep project page as the strongest surface (`app_detail.html`).
2. Keep proof checks honest (no fake universal Verified).
3. Keep publish-first, proof-later (`proof.py`).
4. Keep trust pipeline-written only.

### P1 — Close the Build Detail standard

Make Build Detail match the skeleton above:

1. **Structured sections** when present: Problem · Solution · Features · Contribution · Capabilities.
2. **Capabilities demonstrated** block on the project page, linking each chip to the builder's profile filtered by that capability.
3. Optional **demo URL** + multi-screenshot only if they increase proof (not decoration).

### P2 — Capability-driven discovery (the strategic hole)

1. Capability search endpoint/page: query `Django` →  
   - Projects using Django  
   - People with N published Django builds  
   - Match reason string per person
2. Rank people by **demonstrated project count + proof coverage**, not followers.
3. Profile skill chips become links into that discovery view.
4. Hardest-test instrumentation: capability query results must not sort primarily by stars/followers.

### P3 — Capability model upgrade

1. Map tech/tokens → capability labels (Backend, API Design, Auth, etc.) with evidence project slugs.
2. Still never show a capability with zero published projects.

### P4 — Opportunity (only after P2 works)

1. "Request collab / hire conversation" from Proof CV or capability match.
2. Still no full job board until conversion is measurable.

---

## Feature intake checklist

Before any PR that adds a user-facing feature:

```text
[ ] Which layer? BUILD / PROVE / CAPABILITY / DISCOVER / OPPORTUNITY
[ ] Which of the four product questions does it answer?
[ ] Does it make capability more believable? (yes/no — if no, justify or cut)
[ ] Does it increase popularity bias? (if yes, redesign)
[ ] Can we measure it with a non-vanity metric?
[ ] Would Developer B still beat Developer A after this ships?
```

---

## One-line wall quote

> **BlaqVibes must make capability more believable.**

Everything else is costume until that is true.
