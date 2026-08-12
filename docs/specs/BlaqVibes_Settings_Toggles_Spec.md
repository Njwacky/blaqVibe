# Settings with Toggles — Spec (5 Whys)

**User wants:** Most things have On/Off toggles, so vibe coders get 1-click auto, pros can tweak.

**5 Whys:**
1. Why toggles not form fields? Toggle = 1 tap, not typing. Vibe coders hate forms.
2. Why most things auto? 90% should just work: language %, Nolo, scan, thumbnail, star_cost suggest.
3. Why still toggles? Power users want off: e.g., turn off trading (make free), turn off Nolo if private vibe.
4. Why backend not just JS? Toggle state is per-user, stored in Profile, checked backend before action.
5. Why at scale? 10k users × 10 toggles = personalized feed, no support tickets.

**Toggles (User Settings, all On by default, stored in Profile):**

| Toggle | Default | What it does backend |
|--------|---------|----------------------|
| Auto language detect | On | If Off → use manual tech_stack only, don't run detect_languages |
| Nolo auto-review | On | If Off → skip nolo_review task, scan_report.nolo_review = null |
| Auto thumbnail | On | If Off → don't Playwright screenshot, use generic |
| Allow trading (charge stars) | On | If Off → star_cost forced 0, download free |
| Email on trade/review | On | If Off → don't send_mail |
| Show language % to others | On | If Off → language_stats hidden in detail |
| Allow forks | On | If Off → fork button hidden, POST blocked |
| Allow PRs | On | If Off → PR create blocked |
| Comments | On | If Off → comments hidden, POST blocked |
| Reviews | On | If Off → reviews hidden |

**Global Admin Toggles (SiteSettings singleton, superadmin only):**
- Maintenance mode, ClamAV, R2, Search, PWA

**UI:** iOS-style switches, instant save via fetch POST (no reload), crush silently.
