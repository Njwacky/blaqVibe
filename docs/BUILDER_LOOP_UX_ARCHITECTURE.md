# BlaqVibes Builder Loop UX Architecture

> **Execution plan and architectural contract.**
>
> This document turns the current BlaqVibes product architecture into exact UX and code changes. It is intentionally specific: every change names the surface, the likely template/view/static layer, the user problem it solves, the desired behaviour, and the acceptance criteria.
>
> **Product goal:** make a first-time visitor understand BlaqVibes in seconds, make a builder reach a meaningful build action without navigating a dashboard, and make every published project create the next interaction.
>
> **Core loop:** **BUILD → SHOW → REMIX → COMPETE → REPEAT**
>
> **Primary identity:** builders and projects first. AI, XP, rankings, payments and utilities support the loop; they do not define it.

---

## 1. Non-negotiable architecture

BlaqVibes must behave like a **builder network**, not a dashboard, AI generator, marketplace, or clone of GitHub.

```text
                    BLAQVIBES
                       │
             ┌─────────┴─────────┐
             │                   │
          BUILDERS           PROJECTS
             │                   │
             └───────┬───────────┘
                     │
          BUILD → SHOW → REMIX
                     │
                COMMUNITY
                     │
          COMPETE → REPUTATION
                     │
                   REPEAT
```

### Product hierarchy

1. **Project** — what was built.
2. **Creator** — who built it.
3. **Evidence** — files, README, preview, trust, history.
4. **Build method** — human-built, AI-assisted, AI-generated, remixed.
5. **Lineage** — where the idea came from and where it travelled.
6. **Community** — stars, reviews, remixes and follows.
7. **Commercial action** — Buy & Unlock when applicable.
8. **Gamification** — XP, rank, badges and challenges as supporting signals.

Never reverse this order on a public project surface.

---

# 2. Change Area A — Home / Projects feed

## User problem

A visitor should immediately see **what people are building**. The page must not feel like a personal analytics dashboard.

## Where

- `gallery/views.py` — feed/home data assembly.
- `templates/gallery/base.html` — primary navigation.
- The current projects/feed template rendered by the gallery home view.
- Related feed card CSS under `static/gallery/css/`.
- Feed JavaScript only where progressive enhancement is necessary.

## How it changes

### A1 — First viewport

The first viewport should contain:

```text
WHAT PEOPLE ARE BUILDING

[Project card] [Project card]
[Project card] [Project card]

Discover builders →
```

For authenticated users, personalization may appear below the first project row. Do not lead with:

- XP
- rank
- streaks
- sales
- notifications
- challenges statistics
- personal analytics

### A2 — Project cards

Every project card should answer in roughly one glance:

- What is it?
- Who built it?
- What kind of project is it?
- Build method, if relevant.
- Trust/safety state.
- Remix count when non-zero.
- Primary action.

Preferred card hierarchy:

```text
[PREVIEW]
Project title
@builder
One-line explanation
[BUILD METHOD] [CHECKED/STATUS] [⑂ 4 remixes]

View project →
```

### A3 — Empty states

An empty feed must explain what the network is for and offer a useful action:

> No projects yet. Start something worth showing.
>
> **Build something →**

Never fill an empty state with fake activity.

## Acceptance criteria

- A signed-out visitor sees projects before account utilities.
- A builder can reach `/build/` from the feed in one obvious action.
- Project cards link to project pages, not internal dashboards.
- Gamification is not the dominant visual hierarchy.

---

# 3. Change Area B — Project page conversion

## User problem

The project page is the product's most important page. A visitor must understand the project and decide what to do next without hunting through tabs or panels.

## Where

- `templates/gallery/app_detail.html`
- Project detail view in `gallery/views.py`
- Project-specific styles in `static/gallery/css/`
- Existing project history/remix/trust components
- Existing share-card implementation in `gallery/share_card.py`

## Required page structure

```text
PROJECT
│
├── WHAT IS THIS?
├── WHO BUILT IT?
├── CAN I SEE IT?
├── EVIDENCE
│   ├── Preview
│   ├── Files
│   ├── README
│   └── Trust
├── BUILD METHOD
├── WHAT CHANGED?
│   └── History
├── WHERE DID IT COME FROM?
│   └── Remix lineage
├── COMMUNITY
│   ├── Stars
│   ├── Reviews
│   └── Remixes
└── NEXT ACTION
    ├── Remix
    ├── Share
    └── Buy & Unlock (paid projects only)
```

## B1 — Hero

The hero must establish the project before any metadata:

```text
PROJECT TITLE
What this project does in one clear sentence.

by @creator

[Preview] [Trust status] [Build method]

[⑂ REMIX]   [⧉ SHARE]
```

The title and explanation are more important than stars, XP or badges.

## B2 — Evidence grid

Use the existing project evidence architecture, but make the evidence visually scannable.

Every evidence item should have a clear state:

- Available → open/view it.
- Not available → explain why.
- Checked → show the trust state.
- Unknown → say unknown.

Never imply a project is runnable when no preview exists.

## B3 — Build method

Show provenance as factual metadata:

- Human-built
- AI-assisted
- AI-generated
- Remixed

For AI-assisted/AI-generated projects, details remain secondary to the project evidence.

Do not use language such as:

- “AI app generator”
- “AI creation platform”
- “prompt-powered project”

unless a specific feature genuinely requires it.

## B4 — Next action

The page should always offer a meaningful next step.

Priority:

1. **Remix** if remixing is allowed.
2. **Share** for public projects.
3. **Buy & Unlock** for paid projects.
4. Follow/star/review as secondary actions.

The Remix action must be visible without opening a menu.

## Acceptance criteria

A first-time visitor can answer all of these without leaving the page:

- What is this?
- Who made it?
- Can I see it?
- Is it safe/checked?
- How was it built?
- Was it remixed?
- What changed?
- Can I remix it?
- Can I buy it?

---

# 4. Change Area C — Remix becomes the social primitive

## User problem

A normal “fork” is a technical operation. BlaqVibes needs remixing to become a social interaction.

## Where

- `gallery/remix_stats.py`
- `gallery/views.py`
- `gallery/urls.py`
- `templates/gallery/app_detail.html`
- Feed/project cards
- `/app/<slug>/forks/` family-tree surface
- Build workflow

## C1 — Remix CTA

Replace generic fork emphasis with human language:

> **Remix this project**
>
> Start with the idea, change it, and publish your version.

The action should preserve `forked_from` and existing provenance.

## C2 — Remix flow

```text
DISCOVER
   ↓
REMIX
   ↓
CHANGE
   ↓
PUBLISH
   ↓
ORIGINAL CREATOR SEES IT
   ↓
MORE DISCOVERY
```

After publishing a remix, the user should see:

> You remixed **Project X** by **@creator**.
>
> Your version is now part of the project's remix family.

## C3 — Original creator notification

When a public remix is published, create or reuse the existing notification system so the original creator receives a useful signal.

The notification should link directly to the remix/project page.

Do not notify on unpublished drafts.

## C4 — Family tree

Keep the current family-tree architecture. Improve presentation so the relationship is obvious:

```text
ORIGINAL
  │
  ├── Remix A
  │     └── Remix A2
  │
  ├── Remix B
  │
  └── Remix C
```

Show:

- Original creator
- Remix creator
- Generation/depth
- Published status
- Link to each public descendant

Never expose unpublished descendants to strangers.

## Acceptance criteria

- Remix is one of the first actions on a project page.
- A published remix credits its source project.
- The original creator can discover the remix.
- Public family statistics only count published projects.
- Remixing a remix preserves the full lineage.

---

# 5. Change Area D — Build workflow must feel like creation

## User problem

`/build/` must feel like the beginning of a project, not a form or administration screen.

## Where

- `gallery/build_views.py`
- `/build/` template
- Build-specific CSS/JS
- Builder Skill integration

## D1 — Entry screen

Use exactly three entry points:

```text
WHAT DO YOU WANT TO BUILD?

[ START FROM SCRATCH ]
[ REMIX A PROJECT ]
[ USE A BUILDER SKILL ]
```

Then explain the workflow:

```text
CREATE → UPLOAD → SHOW → FEEDBACK → IMPROVE → PUBLISH
```

## D2 — Reduce form anxiety

Do not present every optional field at once.

Progressive disclosure:

1. Project name.
2. What it does.
3. Project files/content.
4. Preview/readme where applicable.
5. Build method/provenance.
6. Optional metadata.
7. Publish.

## D3 — Success state

After publishing:

```text
YOUR PROJECT IS LIVE

[ View project ]
[ Share it ]
[ Remix another project ]
```

The user should not be dumped into an analytics dashboard.

## Acceptance criteria

- Build has one obvious next action at every stage.
- Optional metadata does not block creation.
- Publishing immediately leads back to the project/community loop.
- Skill/remix provenance is retained automatically.

---

# 6. Change Area E — First-time builder activation

## User problem

The biggest retention risk is not missing features. It is a new builder arriving and not knowing what to do.

## Where

- Home/feed templates
- `/build/`
- Authentication redirect logic
- Builder profile/project empty states

## E1 — New builder path

The ideal first session:

```text
LAND
 ↓
SEE REAL PROJECTS
 ↓
OPEN ONE
 ↓
UNDERSTAND IT
 ↓
REMIX OR BUILD
 ↓
PUBLISH
 ↓
SEE YOUR PROJECT
 ↓
SHARE / GET FEEDBACK
```

Avoid sending a new user directly to a dashboard full of statistics.

## E2 — Empty profile

Instead of:

> You have 0 projects, 0 stars, 0 XP.

Use:

> **Your first project starts here.**
>
> Build something, show it, and let other builders react to it.
>
> **Start building →**

Stats can appear after meaningful activity exists.

## Acceptance criteria

- A new user understands the purpose before seeing gamification.
- Empty states always offer creation/discovery actions.
- The first successful publish has an immediate social next step.

---

# 7. Change Area F — Mobile-first execution

## User problem

The product can be architecturally correct and still fail if project discovery, evidence and remix actions are difficult on a phone.

## Where

- `static/gallery/css/`
- `templates/gallery/base.html`
- Project detail template
- Feed/project cards
- Build templates
- Any detail-page JavaScript in `static/gallery/js/`

## F1 — Mobile navigation

Keep the five primary destinations:

**Projects · Discover · Skills · Challenges · Build**

On narrow screens:

- Use a compact navigation bar.
- Keep **Build** visually prominent.
- Move utilities into the account menu.
- Do not add a second competing navigation system.

## F2 — Project page mobile order

On mobile the order must remain:

1. Title
2. What it does
3. Creator
4. Preview/evidence
5. Trust
6. Build method
7. Remix lineage
8. Community
9. Remix/Share/Buy actions

Do not put large statistics above the project explanation.

## F3 — Sticky action bar

For long project pages, use a lightweight sticky action bar containing only the relevant primary action:

- Remix
- Share
- Buy & Unlock

Do not turn it into a dashboard toolbar.

## Acceptance criteria

- Project title and primary action are visible without horizontal scrolling.
- No core action requires hover.
- Cards remain readable on small screens.
- Upload/build controls remain usable with touch.

---

# 8. Change Area G — Sharing should sell the story, not the platform

## User problem

A shared BlaqVibes link should make someone curious about the project before they know anything about BlaqVibes.

## Where

- `gallery/share_card.py`
- `templates/gallery/app_detail.html`
- `templates/gallery/base.html`

## G1 — Share-card hierarchy

The generated card should prioritize:

```text
PROJECT TITLE
What it does

by @creator

[Build method] [Trust]
★ stars   ⑂ remixes

VIEW PROJECT →
```

The brand should be present but restrained.

## G2 — Social metadata

Keep:

- Open Graph title
- Open Graph description
- Open Graph image
- Twitter large image
- canonical URL

Do not expose internal dashboard metrics as the primary social preview.

## G3 — Structured data

If JSON-LD exposes an `Offer`, emit it only when the project is actually paid. Free projects should not be represented as a paid ZAR offer merely because a price field defaults to zero.

## Acceptance criteria

- Shared links explain the project without requiring BlaqVibes context.
- Unpublished projects never generate public share cards.
- Paid/free structured data accurately reflects the project.

---

# 9. Change Area H — AI stays transparent and secondary

## User problem

Users distrust products that appear to hide how something was made. BlaqVibes must do the opposite without becoming an AI product.

## Where

- `gallery/models.py`
- Project publishing/editing flow
- `templates/gallery/app_detail.html`
- AI helper routes and UI
- README/product copy

## H1 — Provenance labels

Use the existing build-method model as the source of truth:

```text
Human-built
AI-assisted
AI-generated
Remixed
```

Do not infer authorship from vibes or hide it for marketing.

## H2 — AI details are evidence

If AI was materially involved, show:

- tool
- short workflow/creation note

Place it below the main project evidence.

## H3 — Nolo

Nolo remains:

> **an assistant for understanding and improving projects**

It should help users:

- explain
- debug
- compare
- understand
- improve
- discover

It should not become the primary “create an app with AI” path.

## Acceptance criteria

- AI provenance is factual and visible.
- AI metadata does not dominate project pages.
- No deceptive human-authorship presentation is introduced.

---

# 10. Change Area I — Gamification becomes a supporting system

## User problem

XP, ranks and badges can accidentally make the product look like a game dashboard instead of a builder network.

## Where

- Today/pulse templates
- Profile/reputation templates
- Challenge surfaces
- Feed cards
- Notification copy

## I1 — Public hierarchy

Use:

```text
PROJECT → CREATOR → ACTION
```

before:

```text
XP → RANK → BADGES
```

## I2 — Where gamification belongs

Gamification is appropriate in:

- Challenges
- Personal progress
- Reputation
- Competitive views
- Achievement moments

It should not occupy the first visual block of the public feed or project page.

## Acceptance criteria

- Removing XP visually would not make a project page confusing.
- A visitor understands the project without knowing the ranking system.
- Challenges create action rather than merely displaying points.

---

# 11. Change Area J — Today becomes a short return loop

## User problem

Today should answer “what happened and what should I do next?” without becoming a dashboard.

## Where

- Today/pulse view and template
- Existing `gallery/views.py` pulse logic
- Notifications and remix discovery components

## Required order

```text
WHAT ARE PEOPLE BUILDING?
        ↓
WHAT HAPPENED TO YOUR WORK?
        ↓
WHAT CAN YOU REMIX?
        ↓
WHAT SHOULD YOU BUILD NEXT?
```

The page should be short enough to understand quickly.

## Primary actions

- View a project.
- Remix a project.
- Continue your project.
- Start a new project.

Stats remain supporting information.

---

# 12. Change Area K — Sales remain buyer-facing

## User problem

Commercial features can accidentally make BlaqVibes look like a creator payout platform.

## Where

- `gallery/payments.py`
- `users/views.py`
- `templates/users/sales_dashboard.html`
- Project buy box
- Payment tests

## Required money flow

```text
BUYER
  ↓
PAYSTACK
  ↓
VERIFIED PAYMENT
  ↓
SALE RECORDED
  ↓
PROJECT UNLOCKED
```

There is no creator cash-out path.

## Buyer-facing buy box

Before payment show:

- what the buyer receives
- file count
- preview availability
- trust/safety status
- creator
- usage terms
- price
- payment method

## Seller-facing sales

Use only activity language:

- Projects sold
- Unlocks
- Views
- Conversion rate
- Purchases
- Popular projects

Never use:

- Earnings
- Cash out
- Payout
- Creator balance
- Payback

## Acceptance criteria

- A buyer knows exactly what is being purchased.
- A creator can inspect sales activity without seeing a promised balance.
- Payment code cannot initiate a creator transfer.

---

# 13. Change Area L — Builder Skills prove themselves through projects

## User problem

Skills should not become another prompt library. Their value should be proven by builders actually using them.

## Where

- `gallery/skill_models.py`
- `gallery/skill_views.py`
- `/skills/` templates
- Build workflow
- `gallery/models.py`
- Skill/project tests

## Required chain

```text
SKILL
  ↓
SKILL VERSION
  ↓
USE
  ↓
BUILD
  ↓
PROJECT
  ↓
PROOF
```

A project must retain the exact skill version used to start it.

## Skill page hierarchy

```text
WHAT THIS SKILL TEACHES
        ↓
HOW BUILDERS USE IT
        ↓
PUBLISHED PROJECTS
        ↓
START BUILDING
```

Not:

```text
PROMPT
↓
GENERATE
```

## Acceptance criteria

- Every project attribution points to an immutable skill version.
- Skill pages show real published project proof.
- Starting from a skill naturally enters `/build/`.

---

# 14. Change Area M — Profiles prove builder identity

## Where

- `users/views.py`
- Profile templates
- Profile CSS
- Activity/reputation components

## Required profile order

```text
@username
BUILDER

What they build / short identity

Projects · Remixes · Skills · Activity · Reputation
```

Projects and remixes should be more prominent than numeric reputation.

## Profile proof

A strong profile should show:

- Published projects
- Remixes made
- Skills published/used
- Project history/activity
- Reputation signals
- Followers/social context

The profile should answer:

> **“Why should I follow or learn from this builder?”**

---

# 15. Change Area N — Trust becomes visible proof

## Where

- Trust pipeline
- `gallery/models.py`
- `templates/gallery/app_detail.html`
- Trust explanation page
- Scan/status UI

## Trust stack

```text
CREATOR
  +
FILES
  +
README
  +
PREVIEW
  +
SECURITY SCAN
  +
PROJECT HISTORY
  +
REVIEWS
  +
STARS
  +
REMIX LINEAGE
  +
BUILD METHOD
```

Trust must never be a decorative badge disconnected from evidence.

## Required behaviour

When project bytes change:

```text
OLD TRUST
   ↓
CONTENT CHANGED
   ↓
TRUST RESET
   ↓
SCAN
   ↓
NEW TRUST STATE
```

A project must not continue displaying an old verification state after materially changing its content.

---

# 16. Change Area O — Remove accidental AI-first and dashboard-first language

## Where to audit

Run a repository-wide text search for:

```text
AI app generator
AI creation platform
prompt generator
prompt-powered
vibe coding
creator earnings
cash out
cash-out
payout
payback
creator balance
ZAR payout
```

## Action rules

### Remove from primary product identity

- AI creation language
- Prompt-generator language
- Creator payout language
- Dashboard-first headlines

### Keep where technically accurate

- AI-assisted
- AI-generated
- AI tool
- Build method
- Sales
- Buyer
- Unlock
- Internal stars economy

Do not remove historical migration/model terminology merely because it contains old vocabulary. Database history is not product identity.

---

# 17. Change Area P — Performance and resilience

## User problem

A beautiful builder loop is useless if it is slow or fragile.

## Where

- Feed views/querysets
- Remix statistics
- Project detail view
- Share card
- Cached Builder Pulse
- Static assets

## Required engineering rules

### P1 — Feed

Use bounded querysets and `select_related`/`prefetch_related` where relationships are rendered.

### P2 — Remix stats

Keep remix calculations derived from the published graph and avoid N+1 queries when building discovery rails.

### P3 — Project page

Avoid querying the same project, owner or lineage multiple times during one request.

### P4 — Share card

Continue server-rendering sanitized plain text only. Keep caching and unpublished visibility protection.

### P5 — Mobile

Avoid shipping JavaScript for interactions that can be completed with normal links/forms.

---

# 18. Exact implementation sequence

Do **not** implement everything at once. Use vertical slices so every change can be tested and observed.

## Phase 1 — Project page

**Files:**

- `templates/gallery/app_detail.html`
- project detail CSS
- project detail tests

**Goal:** make the project page the strongest surface in the product.

**Done when:** a visitor can understand, trust and act on a project without leaving the page.

## Phase 2 — Remix loop

**Files:**

- `gallery/remix_stats.py`
- remix/build views
- notifications
- project/feed templates
- remix tests

**Goal:** turn remixing into a social loop.

**Done when:** a remix creates visible lineage and the original creator can discover it.

## Phase 3 — First-time builder flow

**Files:**

- `/build/` view/template
- home/feed
- empty profile/project states
- authentication redirect where appropriate

**Goal:** reduce time-to-first-project.

**Done when:** a new builder can go from landing to a meaningful build action with no dashboard detour.

## Phase 4 — Mobile polish

**Files:**

- `static/gallery/css/`
- base template
- project/feed/build templates

**Goal:** make the same loop excellent on small screens.

**Done when:** the primary actions remain obvious and touch-friendly.

## Phase 5 — Sharing + SEO correctness

**Files:**

- `gallery/share_card.py`
- `templates/gallery/app_detail.html`
- `templates/gallery/base.html`

**Goal:** make every shared project tell its own story.

**Done when:** a shared URL explains the project and its creator without requiring prior knowledge of BlaqVibes.

## Phase 6 — Remove remaining product noise

**Files:** repository-wide, with special attention to templates and public copy.

**Goal:** remove accidental AI-first, payout-first and dashboard-first identity.

**Done when:** the first impression is consistently builder/project/community-first.

---

# 19. Measurement architecture

Do not judge these changes only by code completion. Measure behaviour.

## Funnel

```text
VISIT PROJECT
    ↓
OPEN PROJECT
    ↓
SEE EVIDENCE
    ↓
REMIX / BUILD
    ↓
PUBLISH
    ↓
RECEIVE REACTION
    ↓
RETURN
```

## Metrics that matter

### Activation

- Time from first session to first build start.
- Percentage of new builders who start a project.
- Percentage who publish their first project.

### Project quality

- Project-page engagement.
- Preview opens.
- README/evidence interactions.
- Shares.
- Stars/reviews.

### Remix loop

- Remix starts.
- Remix publishes.
- Remix-to-remix depth.
- Original creators viewing remixes.
- Repeat remixers.

### Retention

- Return visits after publishing.
- Builders who publish a second project.
- Builders who remix after publishing.
- Builders who receive and respond to feedback.

## Anti-metrics

Do not optimize the product primarily for:

- raw page views
- XP earned
- badge count
- notification count
- dashboard visits
- AI generation count

Those can rise while the actual builder network gets weaker.

---

# 20. Definition of “best”

The work is successful when BlaqVibes can pass this five-second test:

> **“This is where developers/builders show what they are building, discover other projects, remix ideas, and build a reputation.”**

And this ten-minute test:

```text
I found a project.
↓
I understood it.
↓
I trusted what I could verify.
↓
I saw who built it.
↓
I saw where the idea came from.
↓
I remixed it or started my own.
↓
I published.
↓
The original/community could react.
↓
I had a reason to come back.
```

If a new feature does not strengthen this sequence, it should not become a priority.

---

# 21. Final product rule

**Do not add another major system until the existing builder loop is being used by real people.**

The priority order is:

```text
TRUST
  ↓
PROJECT
  ↓
REMIX
  ↓
BUILD
  ↓
DISCOVERY
  ↓
SHARING
  ↓
SALES
  ↓
REPUTATION
```

The platform should feel increasingly simple as its underlying architecture becomes more sophisticated.

**BlaqVibes should hide complexity from the builder, not expose complexity to the builder.**
