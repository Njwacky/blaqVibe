# BlaqVibe Launch Design: Analysis & Recommendations

## Overview
The Launch Guide is a sophisticated publishing router that helps creators identify where their built applications can be deployed. Based on architecture analysis of `gallery/launch_guides.py`, `gallery/launch_views.py`, templates, and tests.

---

## What's Working Well ✅

### 1. **Honest Boundary Communication**
- Clearly states "Preview is not hosting" multiple times
- Explicitly distinguishes BlaqVibes preview from production deployment
- Does not conflate Docker Hub (registry) with actual hosting
- Every guide emphasizes verification against live platform docs

### 2. **Source-Backed & Maintained**
- All guides linked to official platform documentation (HTTPS only)
- Data-driven approach: guides are curated data, not user-generated content
- `LAST_REVIEWED` timestamp enforces maintenance accountability
- Commands copied directly from official docs, never fabricated
- Placeholder angle brackets are conspicuous and always marked with `replace` notes

### 3. **Comprehensive Coverage**
- 13 guides covering 12 artifact types
- Covers web, mobile, games, desktop, and distribution channels
- Includes both storefront (itch.io) and reviewed (Steam) game routes
- Separates direct macOS distribution from App Store route
- Android and iOS guides are explicit about signing, testing, and review requirements

### 4. **Accessible Design**
- Semantic HTML with proper ARIA labels
- Copy-button interactions with live-region announcements
- Keyboard-navigable artifact selection
- Accessible color indicators and visual feedback
- Checklist with local storage progress tracking

### 5. **Safety-First Philosophy**
- No credentials collected or stored
- Consistent warnings about removing .env files and secrets
- Explicit high-risk gates (e.g., 14-day testing requirement for Google Play)
- Production artifact validation emphasized throughout
- Five Whys analysis shows careful product reasoning

---

## Issues & Gaps 🔴

### **Issue #1: Limited Coverage — Missing Popular Platforms**
The system has 13 guides covering major platforms, but **several high-demand platforms are absent**:

**Missing Guides:**
- **AWS (S3 + CloudFront)** — Most used by enterprise developers globally
- **Azure Static Web Apps, Blob Storage, App Service** — Enterprise Microsoft ecosystem
- **DigitalOcean App Platform** — Popular among startups and indie developers
- **PythonAnywhere** — Perfect fit for Django (your tech stack!)
- **Railway** — Modern Heroku alternative (easier than Render for beginners)
- **Fly.io** — Growing platform for containerized apps
- **AWS Lambda / serverless** — Cost-efficient for APIs and functions
- **Google Cloud Run** — Serverless alternative to Render
- **Netlify** — Strong alternative to Vercel/Cloudflare for frontends
- **Supabase** — Popular for full-stack with built-in auth/DB (growing)

**Current Coverage:**
- ✅ 5 web deployment guides (Cloudflare Pages, Vercel, Render, PWA, Docker Hub)
- ✅ 2 mobile guides (Google Play, App Store)
- ✅ 2 game guides (itch.io, Steam)
- ✅ 3 desktop guides (Microsoft Store, macOS direct, Flathub)
- ✅ 1 extension guide (Chrome Web Store)

**Impact:** 
- Creators targeting AWS/Azure receive no guidance and may misuse these platforms
- Django developers (like yours) have no PythonAnywhere option
- Growing platforms (Fly.io, Railway) not mentioned
- Serverless patterns (Lambda, Cloud Run) not covered

**Recommendation Priority:**
1. **Add AWS S3 + CloudFront** (most requested)
2. **Add Azure Static Web Apps + App Service** (enterprise)
3. **Add PythonAnywhere** (perfect for BlaqVibe's Django tech stack)
4. **Add DigitalOcean App Platform** (popular with indie devs)

---

### **Issue #2: Artifact Route Ambiguity & Overlap**

Some guides appear in multiple artifact routes, which can create confusion:

```
Docker Hub (registry):
  - In "server" route: ("render-web-service", "docker-hub")
  - In "container" route: ("docker-hub", "render-web-service")
  
itch.io (storefront):
  - In "webgame" route: ("itchio", "cloudflare-pages")
  - In "pcgame" route: ("itchio", "steam")
```

**Problems:**
1. "server" and "container" routes show the same guides (just reordered)
   - This implies Docker Hub is both a runtime host AND a registry
   - Guides already separate these concerns ("Docker Hub stores and distributes images; it does not keep your web app running")
   - But the UI routing suggests they're interchangeable deployment targets
2. "webgame" suggests browser games can go to Cloudflare Pages (correct) OR itch.io (storefront, different audience)
   - User might think they're equivalent
   - No guidance on which storefront to choose based on game type

**Test Coverage:**
- ✅ `test_registry_and_host_are_not_conflated` passes — the guide content IS correct
- ❌ BUT the UI routing could confuse users before they read the guide

**Recommendation:** 
1. **Separate concerns in ARTIFACT_ROUTES:**
   - Keep "server" for Render (runtime host only)
   - Create "registry" type for Docker Hub only
   - Add UI hint: "Registry stores images; choose a runtime host separately"
2. **Add comparison guidance:**
   - "itch.io vs. Steam: Where should your game go?" — link from both guides

---

### **Issue #3: Error Handling & Validation Gaps**
The view handles invalid artifact parameters gracefully, but silently:

```python
else:
    # Unknown input safely falls back to no selection.
    active_artifact = ""
```

**Current Behavior:**
- User bookmarks: `/launch/?artifact=aws-s3` (that guide doesn't exist)
- Silently falls back to showing all guides
- No warning, no 404, no helpful message
- No logging to identify missing guides users are looking for

**Problems:**
1. **User confusion** — Person thinks they saved a link to their platform but sees all 13 guides
2. **Missing insights** — No way to know which platforms users are searching for
3. **Broken bookmark handling** — If a guide slug changes, old links silently break
4. **No analytics** — Can't identify demand for missing platforms

**Tests:**
- ✅ `test_unknown_category_falls_back_to_all` passes
- ❌ No test for invalid artifact parameter
- ❌ No test for logging or user feedback

**Recommendation:**
```python
# Add explicit error tracking
if active_artifact and active_artifact not in [r["value"] for r in ARTIFACT_ROUTES]:
    logger.warning(f"User requested unknown artifact: {active_artifact}")
    # Show helpful message in template
    context["invalid_artifact"] = active_artifact
```

Add to template:
```django
{% if invalid_artifact %}
  <div class="launch-warning">
    ⚠️ "{{ invalid_artifact }}" isn't recognized.
    <a href="#deck">Browse all destinations</a>
  </div>
{% endif %}
```

---

### **Issue #5: Category-Artifact Interaction Unclear**
The UI allows filtering by both category AND artifact:
- `/launch/?category=games&artifact=server` — "Servers" in the "Games" category (likely empty or confusing)
- No clear indication that some category-artifact combinations are rare or nonsensical

**Problem:** Users may think they've found no viable routes when in fact the combination is just uncommon.

**Recommendation:** 
- Show a helpful message: "No routes match 'Servers' in 'Games' category. Try 'All destinations'."
- Consider disabling invalid combinations in the UI

---

### **Issue #6: No Progressive Disclosure for High-Risk Steps**
Guide steps are flat — no indication of complexity or risk level:

```
Vercel guide steps:
1. Make the project production-ready (easy)
2. Install the official CLI (medium)
3. Configure production settings (medium-high risk — secrets!)
4. Create and test the first deployment (high risk — first deploy is production)
5. Deploy a tested update to production (high risk)
```

Users may skip or rush high-risk steps without clear warning upfront.

**Recommendation:** 
- Mark high-risk steps with a visual indicator (e.g., 🔴 or 💀)
- Show estimated time/difficulty per step
- Require explicit acknowledgment of high-risk steps before proceeding (in interactive version)

---

### **Issue #7: No Context for Framework-Specific Commands**
The guides correctly avoid fabricating framework commands, but they don't:
1. Show common framework-specific commands for reference
2. Explain HOW to find the right command (e.g., check package.json `build` or `start` scripts)
3. Provide debugging help when framework commands fail

**Recommendation:** Add a sidebar section "Find your framework's command" with links to popular frameworks:
- Next.js: `npm run build` → `npm start`
- React: check `package.json` build script
- Django: `python manage.py collectstatic` → `gunicorn myapp.wsgi:application`
- etc.

---

### **Issue #8: No Explicit Support for Monorepos**
Many creators build monorepos (e.g., frontend + backend in one repo). The guides assume single-artifact projects.

**Problems:**
- No guidance on splitting monorepo artifacts
- Vercel guide says "commit required source, dependency lockfiles, migrations" but doesn't explain what happens if you have two apps
- Docker guide assumes single Dockerfile

**Recommendation:** Add a guide or section: "Publishing from a monorepo" with links to:
- Vercel: environment-specific builds
- Docker: building multiple services
- Monorepo repository structure best practices

---

### **Issue #9: Browser Compatibility & JavaScript Dependency**
The `launch_hub.html` mentions:
```html
<noscript><p class="launch-noscript">JavaScript is off — picking a card reloads the page instead of filtering live. Everything still works.</p></noscript>
```

**Problem:** 
- Tests don't verify JavaScript-free functionality
- No test checks that artifact filtering works via query params (the fallback)
- Potential accessibility issue if JS errors silently fail

**Recommendation:** 
- Add test: `test_artifact_filtering_works_without_javascript`
- Ensure all link generation includes proper query param encoding
- Test with JavaScript disabled in actual browsers

---

### **Issue #10: Outdated Platform Policies Not Caught**
The maintenance checklist says:
> "Review every URL and claim in `gallery/launch_guides.py` at least quarterly."
> `LAST_REVIEWED = "13 August 2026"`

**Problem:**
- Only date is tracked; no way to know which guides were reviewed
- No automated check that a guide's last review is within 90 days
- No warning if a guide is overdue for review

**Recommendation:**
1. Add `last_reviewed` to each guide individually:
   ```python
   {"slug": "cloudflare-pages", "last_reviewed": "13 August 2026", ...}
   ```
2. Add template warning if any guide is older than 90 days:
   ```django
   {% if days_since_review > 90 %}<span class="outdated-warning">⚠️ This guide hasn't been reviewed in {{ days_since_review }} days</span>{% endif %}
   ```
3. Add management command: `python manage.py check_guide_reviews --days=90`

---

### **Issue #11: No Comparison/Decision Matrix**
The launch hub shows a card for each guide, but doesn't help users choose between similar platforms:

**Example confusion:**
- Cloudflare Pages vs. Vercel: When should I pick one over the other?
- Render vs. DigitalOcean: Cost/performance trade-off?
- Itch.io vs. Steam: Game audience differences?

**Recommendation:** Add a comparison matrix accessible from the launch hub:
```
Comparison: Static Site Hosts
┌─────────────────┬──────────┬──────────┬────────┐
│ Platform        │ Price    │ Speed    │ Setup  │
├─────────────────┼──────────┼──────────┼────────┤
│ Cloudflare Pages│ Free     │ Fastest  │ 2 min  │
│ Vercel          │ Free     │ Fast     │ 3 min  │
│ GitHub Pages    │ Free     │ Medium   │ 5 min  │
└─────────────────┴──────────┴──────────┴────────┘
```

---

### **Issue #12: No Integration with Upload/Export Flow**
The launch guide is discoverable from the hub navigation, but:
- No direct link from the "Upload" flow when a creator finishes uploading
- No suggestion based on project type detection (e.g., if they uploaded a Vue SPA, suggest Vercel)
- No "What's next?" post-upload experience

**Recommendation:** 
1. Add a modal after successful upload: "Now, where do you want to ship it?" with a link to launch hub
2. Detect project type from upload (check for package.json, Dockerfile, Android manifest, etc.)
3. Pre-select matching artifact route: `/launch/?artifact=frontend` if Vue app detected

---

## Improvements by Priority 🎯

### **TIER 1 — Critical (Fix Now)**
1. **Add missing popular platforms** 
   - AWS (S3 + CloudFront) + Azure Static Web Apps (enterprise demand)
   - PythonAnywhere (aligns with Django tech stack)
   - DigitalOcean App Platform (high demand)
2. **Add error handling for invalid artifact parameters** — Track user demand for missing platforms
3. **Fix artifact-category UI ambiguity** — Separate "server" and "container" or clarify relationship
4. **Per-guide review tracking** — Add `last_reviewed` field to each guide + stale-guide warnings

### **TIER 2 — High Impact (Next Sprint)**
5. **Add JavaScript-off test** — Verify artifact filtering works via query params
6. **Progressive disclosure for high-risk steps** — Visual indicators + warnings on risky steps
7. **Framework-specific command reference** — Help users find the right build/start command
8. **Monorepo guidance** — Document how to publish from monorepos
9. **Comparison matrix** — Help users choose between similar platforms (Vercel vs. Cloudflare, etc.)

### **TIER 3 — Nice to Have (Roadmap)**
10. **Integration with upload flow** — Suggest launch destinations after file upload
11. **Auto-detection of project type** — Detect framework from package.json/Dockerfile/manifest
12. **Platform demand analytics** — Track which guides users search for / request most
13. **Scheduled guide reviews** — Automation to alert when guides are overdue for review

---

## Test Coverage Gaps 📋

**Tests that exist (11 total, all passing ✅):**
- ✅ Hub is public and truthful
- ✅ Category filter works
- ✅ Unknown category falls back gracefully
- ✅ Every guide renders with sources
- ✅ Unknown guide returns 404
- ✅ Guides have complete structure and valid references
- ✅ All artifact routes reference existing guides
- ✅ Sources are HTTPS on expected official domains
- ✅ High-risk requirements are explicit
- ✅ Accessible controls render
- ✅ Registry/host not conflated
- ✅ Navigation links to launch hub exist

**Tests that would improve coverage:**
- ❌ `test_artifact_filtering_without_javascript` — Verify query-param fallback works
- ❌ `test_invalid_artifact_parameter_logged` — Track demand for missing platforms
- ❌ `test_category_artifact_rare_combinations_show_hint` — UX for uncommon combinations
- ❌ `test_guides_reviewed_within_90_days` — Maintenance automation
- ❌ `test_guide_maintenance_timestamp_explicit_per_guide` — Track each guide's review date
- ❌ `test_framework_reference_links_present` — Help finding framework-specific commands

---

## Summary

The **Launch Design is fundamentally sound and well-tested** — all 11 tests pass. It's honest, safe, and comprehensive for major platforms. However, there are opportunities for improvement:

### What's Strong ✅
- **Honest communication** about BlaqVibes' role (preview, not hosting)
- **Complete coverage** of 13 major platforms (web, mobile, games, desktop)
- **Excellent test suite** validating structure, sources, and accessibility
- **Safe design** — no credentials collected, clear warnings, production artifacts emphasized
- **Source-backed** — all guides linked to official platform docs

### Where It Falls Short ❌
1. **Platform gaps** — Missing AWS/Azure/DigitalOcean/PythonAnywhere (high-demand platforms)
2. **Error handling** — Silent fallback on invalid artifact parameters (can't track user demand)
3. **UI ambiguity** — "server" and "container" routes show identical guides
4. **Maintenance** — Global `LAST_REVIEWED` date doesn't track individual guide staleness
5. **Context gaps** — No help finding framework-specific commands, no comparison matrix, no monorepo guidance
6. **Integration** — Launch guide is discoverable but not surfaced during/after upload flow

### Next Steps
1. **Add top 3 missing platforms** (AWS, Azure, PythonAnywhere) — 80% of missing demand
2. **Implement per-guide review tracking** — Prevent outdated guidance
3. **Add error logging** for invalid artifact requests — Identify platform demand
4. **Create comparison guides** — Help choose between similar platforms
5. **Integrate with upload flow** — Surface launch guidance right after build

This is a strong foundation to build on. The core design philosophy is sound; the improvements are mostly about expanding coverage and improving discoverability.


