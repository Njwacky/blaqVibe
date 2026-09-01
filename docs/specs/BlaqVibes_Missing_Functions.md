# BlaqVibes — Missing Functions Checklist (After Profiles + Queue + Side Panels)

**Date:** 17 Aug 2026 | **App Status:** Feed, Upload ZIP + Queue (ClamAV → Vuln → Publish), Tree + README + Comments, Profiles/Follow, Moderation, Real Git Daemon (clone + push), Admin Dashboard Charts | **Requested:** Real Git Daemon + Admin Dashboard Charts ✅ Done

---

## ✅ DONE (What you have)
- [x] Feed with filters (category, kind, AI, search)
- [x] Snippet HTML/CSS copy + Full App ZIP download
- [x] **Real git daemon** — `git clone` AND `git push` work at `/git/<user>/<slug>.git` (Dulwich smart-HTTP). Pull follows the download rules (free vibes clone anonymously; paid vibes 401 until credentials with a Trade/Sale arrive). Push requires Basic auth (password or revocable git token) as owner/co-owner — never a browser session — and a pushed HEAD becomes the current ZIP and re-enters the scan queue before going live again. Repos are built from stored ZIPs into a version-snapshot commit chain (`gallery/git_daemon.py`).
- [x] Mandatory README + sanitized markdown + file tree (200KB preview, path traversal blocked)
- [x] Comments (threaded, sanitized, ratelimited)
- [x] Profiles `/u/nolo.ai` + Follow + Edit (bio, avatar, location, GitHub) + Stars tab
- [x] My Vibes (`/my-vibes/`) + ScanJob queue (FIFO, acks_late, every app checked)
- [x] Moderation Queue (`/moderation/queue/` staff)
- [x] “We’ll tell you when it’s uploaded” banner + poll + messages + email-ready
- [x] S3/R2 presigned URLs (5-min), ClamAV + secrets + vuln workers (Celery)
- [x] Side Panels Demo (left nav + main + right inspector, no scroll hell)
- [x] Light/Dark toggle (new demo)
- [x] **Admin dashboard charts** — server-rendered SVG (zero third-party JS): clones/day, trades/day, ★ volume/day, signups/day, uploads/day, stacked scan outcomes/day, top vibes by stars and by clones, quarantine rate. Charts only draw from append-only logs (`CloneEvent`, `Trade`, `ScanJob`, `date_joined`) — cumulative counters are never charted because they have no timestamps.

---

## 🔴 CRITICAL MISSING — Blocks Launch

| # | Function | Why Missing Hurts | 5 Whys Quick | Effort |
|---|----------|-------------------|--------------|--------|
| 1 | **Email Notify on Queue Done** | User closes tab, never knows vibe is live | Why email not just toast? Toast dies on close. Why not now? Needs `send_mail` + Celery on `finalize_publish`. Why backend? Secrets stay backend. | 1 day |
| 2 | **Edit / Re-upload Version** | Can’t fix bug after publish; must delete & re-upload loses stars | Why version? Git-like `v1.1.0`. Why not overwrite? History matters. | 2 days |
| 3 | ~~**Delete & Report Vibes**~~ | ✅ DONE 01 Sep 2026 — `/moderation/reports/` triage: open/ignored/resolved lifecycle, outcome per action, moderator fan-out on creation, owner notify on quarantine/removal, AdminLog audit, sibling auto-resolve, report dedupe per user/project (24h), admin-only remove/delete. See `gallery/reports.py`, `templates/gallery/reports_queue.html`. | 1 day |
| 4 | ~~**Real Git Daemon (clone/push)**~~ | ✅ DONE — see above | Dulwich smart-HTTP, Basic auth + git tokens, push re-scans via the queue | done |
| 5 | **Search v2 (Full-text + Trending)** | `icontains` slow at 1k, no “Trending” or `tech:Django` | Why Postgres `SearchVector` + `trigram`? GIN index. Why trending? `clones*3 + stars`. | 2 days |
| 6 | **Pagination + Performance** | Feed loads all, 10k = OOM. Need 12/page + `select_related` | Why 12? Grid 3×4. Why not infinite? SEO needs pages. | 0.5 day |

## 🟠 IMPORTANT — Hurts UX / Trust

| # | Function | Why | Effort |
|---|----------|-----|--------|
| 7 | **Collections / Bookmarks** | Save vibe for later, like GitHub stars but private | 1 day |
| 8 | **Stars Persistence + Sort by Stars** | Stars exist but no sort feed by `?sort=stars` | 0.5 day |
| 9 | **Notifications Center (in-app bell)** | Central place for “your vibe approved/quarantined, new follower, new comment” | 1.5 days |
| 10 | ~~**Admin Dashboard (charts)**~~ | ✅ DONE — see above | server-rendered SVG: clones/day, top vibes, quarantine rate | done |
| 11 | **OG Images + SEO** | Share `/app/stock-app-vibes` on X/WhatsApp needs `og:image` screenshot | 1 day |
| 12 | **Mobile Polish + Light Mode in Django** | Demo has light/dark toggle, Django still dark-only. Need `localStorage` theme | 1 day |
| 13 | **Rate Limit UI + Upload Progress Bar** | 100MB ZIP with no progress = user retries, double queue | 1 day |
| 14 | **API v1 (`GET /api/vibes?q=&tech=`)** | Frontend, CLI, mobile need it | 2 days |
| 15 | ✅ **Username rename (PUBG rule) + name style** — done 21 Aug 2026, see `BlaqVibes_Rename_5Whys.md`. Rename card: free while Pro or 100 ★ burned, 30-day cooldown, old name reserved 90 days, old `/u/<name>/` links 302-redirect, reserved-word list gates signup AND rename. Name style (font/color/size/anime fx): whitelisted slugs only, 20 ★ per change (free while Pro), rendered server-side — never user CSS. Fixed alongside: feed cards had lost the creator link + full title (two tests red on master). | done |

## 🟡 NICE-TO-HAVE — Later

| # | Function | Effort |
|---|----------|--------|
| 15 | Payments (Paystack Pro paywall) | 3 days |
| 16 | AI Import (paste Lovable/v0 link) | 2 days |
| 17 | CLI `npx blaqvibes clone` | 2 days |
| 18 | Sitemap.xml + JSON-LD + PWA | 1 day |
| 19 | Tests (pytest) + Sentry + Backups | 2 days |
| 20 | Real-time chat / comments websocket | 3 days |

---

## 📋 NEXT SPRINTS (Recommended Order)

**Sprint 1 (This week):** 1 Email Notify + 3 Report/Delete + 6 Pagination  
**Sprint 2:** 5 Search v2 + 7 Collections + 12 Light Mode Django + 8 Sort  
**Sprint 3:** 14 API + 9 Notifications Center + 11 OG Images  

---

## 🎛️ Light/Dark — Done

Demo `BlaqVibes_Demo_SidePanels_LightDark.html` now has:
- CSS variables `--bg/--card/--line/--text` toggled via `data-theme="dark|light"`
- `🌙/☀️` button in sidebar + topbar → `toggleTheme()` + toast
- Sidebar, cards, inputs, right panel all switch (no quirks, no secrets in JS)

**To apply to Django:** Add `localStorage.theme` + `document.body.setAttribute('data-theme', ...)` in `base.html` + `prefers-color-scheme` fallback. 0.5 day.

---

**Pick 1-2 from 🔴 to implement next with 5 Whys + full code (no shortcuts).** Which?
- Reply: “Build Search v2” or “Build Email Notify” or “Build API v1”
