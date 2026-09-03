# BlaqVibes — Retention & "accidental hack" audit

**Date:** 2026-09-03 · **Branch:** `arena/01a06809-blaqvibe` @ `9fa5f9c`
**Method:** ran the real app (migrate → `seed_demo` → `runserver`), ran the full suite
(`662 tests, 1 failure`), and drove the publish / edit / fork / trade paths with throwaway
accounts. Every claim below is either a code reference (`file:line`) or something I reproduced.

---

## 0. Short answer: is it boring?

**The idea isn't boring. The product currently is — because nothing happens in it.**

The engine room is genuinely better than most indie projects: real scan pipeline, real trust
tiers that only the pipeline can write, real git smart-HTTP, a star ledger that actually
reconciles, an honest "preview is not hosting" stance, and a voice ("MyVibe Vol.1 → Vol.2",
★/Rands, Mzansi) that no competitor has. That's the part people would miss if it disappeared.

But here is what a stranger actually meets today:

| What they see | Why it kills the mood |
|---|---|
| 7 seeded vibes, **all owned by `@blaq`**, all "dashboard / landing page / table" templates | Reads like a one-man template dump, not a community |
| Cards with **no thumbnail, no date, no comment count** — a CSS "app-mock" placeholder | Nothing to look at, no proof of life |
| "No live preview — browse the file list" on the non-runnable ones | The gallery's promise ("vibes") is mostly a file browser |
| **Battle** = 2 random cards out of 7, header promises "+1 ★", the vote then says *"project stars are unchanged"* | A game whose reward the UI contradicts; you re-see the same pair in 3 clicks |
| **Challenges** tab: empty list, but hardcoded copy promising *"Build Vol.2 … #challenge-week-12"* | A promise with nothing behind it |
| No activity anywhere: no "published today", no recent comments, no trending velocity | No reason to come back tomorrow |

And the part that matters most: **if they do publish, nothing appears.** Not "in 5 minutes" —
*nothing, ever, silently* (§1.1 and §1.2). So the one action that would make the product feel
alive is the one action that currently dead-ends.

**Boring is a symptom. The disease is: the first-run loop has no ending, and the return loop has
no heartbeat.** Fix those two and the same code feels completely different.

---

## 1. The churn killers, ranked

### 1.1 🚨 Editing a published vibe silently deletes it from the site (P0)
`gallery/views.py:898` sets `p.status = 'pending'` on **every** edit. For a snippet (no ZIP)
there is no rescan path that ever sets it back — the view just says
*"✓ Vibe updated!"* (`gallery/views.py:928`).

Reproduced:
```
after publish: published | anon feed? True
after EDIT:    pending   | anon feed? False | stranger detail: 404 | owner flash: "✓ Vibe updated!"
```
The owner is told it worked. Everyone else gets a 404. It is gone from the feed, from search,
from their public profile. Nothing notifies a moderator. In production with Celery working but
ClamAV absent, the same edit also kills ZIP vibes permanently (§1.4).

**Fix:** never take live content offline for a rescan. Scan a shadow copy; only flip `status`
when the pipeline *decides* (quarantine/hold), not when an edit *starts*. At minimum, restore
`published` on the snippet path after re-running `snippet_evidence` + `apply_trust_grade`.

### 1.2 🚨 A brand-new creator's first publish can never go live (P0)
`gallery/views.py:671`:
```python
if request.user.projects.filter(status='published').count() >= 3:
    project.status = 'published'
```
A new user has 0 published vibes → their snippet stays `pending`. To get 3 published vibes you
must first have 3 published vibes. No `ScanJob` row, no Celery task, no notification — and the
flash message still promises *"we'll tell you when it's uploaded"*.

Studio (your best onboarding surface, `/start/`) publishes a **snippet**, so the flagship funnel
lands exactly here: write code → sign in → publish → gone.

**Fix:** drop the "3 published" heuristic. Snippets already get real evidence from
`trust.snippet_evidence` — auto-publish when that's clean, and send everything else to a real
review queue with a visible ticket ("#4 in line · reviewed by a human within 24h").

### 1.3 🚨 The ZIP most beginners have cannot be uploaded (P0)
Any vibe-coded folder contains `node_modules/`, `.venv/`, or a `.env`. Upload → hard reject:
```
Blocked file/folder in ZIP: node_modules — credentials and tool config never go in a vibe. Remove it and re-upload.
Provide either a ZIP file (full app) or HTML snippet.        ← second, contradictory error
```
(`gallery/validators.py:24-32` for the blocklist, `:104` for the message, `:69` for
"Too many files (N). Max 1000 — possible bomb or node_modules.")

Two errors, neither says *how* to fix it, and `node_modules` gets a **credentials** explanation
that is simply wrong. The user's conclusion is "this site doesn't accept my app".

**Fix:** pre-flight the ZIP in the browser and show a fix-it panel ("we'll drop
`node_modules/`, `.venv/`, `.env` for you — 812 files removed"), or auto-strip and re-scan with
a clear receipt. Rewrite the copy per reason. One error, not two.

### 1.4 🚨 No ClamAV or no Celery worker = nothing ever publishes, silently (P0)
- `scan_zip_with_clamav` → `FileNotFoundError` → `status='pending'` + `clamav: 'unavailable'`
  (`gallery/tasks.py:186-196`), and `finalize_publish` then returns `pending_no_scanner`
  (`gallery/tasks.py:294-296`). **No ZIP upload auto-publishes on a host without `clamscan`.**
- With Redis down, `process_upload_pipeline.delay()` (`gallery/views.py:648`) **blocked the HTTP
  request for ~20 seconds** retrying the broker, then failed silently into the `except`. The user
  still sees *"You're #1 in line"*.

Reproduced: `finalize_publish succeeded: 'pending_no_scanner'`.

**Fix (all three):** (a) broker health check at startup + in `/readyz`; fail the upload loudly
("the scan service is offline, we saved your draft") instead of lying; (b) a watchdog that
re-queues/alarms on `ScanJob.status in ('queued','scanning')` older than N minutes; (c) decide
the ClamAV policy explicitly — if the operator disables it, publish on secret-scan + dep-audit
evidence and say so, rather than silently holding 100% of uploads for a human who never logs in.

### 1.5 The verification email that never arrives = zero stars, forever (P0)
`EMAIL_BACKEND` defaults to the **console** backend (`blaqvibes/settings.py:583`) and
`send_mail(..., fail_silently=True)` (`users/views.py:628`). Ship without SMTP config and:
no verify mail → `email_verified=False` → **no 5★ welcome grant** (`users/wallet.py:29`) →
trading refused (`gallery/economy.py:126`) → tipping refused → the entire money path is dead
for 100% of users, with no error anywhere.

**Fix:** `security_check` must ERROR when `DEBUG=0` and `EMAIL_BACKEND` is the console backend.
Add a `/admin/` panel row: "verified users / pending / mails failed in 24h".

### 1.6 Rate limits keyed on IP will lock out real people (P1)
Every limiter uses `key='ip'` with plain `REMOTE_ADDR` (no proxy/XFF config anywhere): signup
**10/h** (`gallery/views.py:84`), Nolo chat **20/h** (`gallery/views_community.py:63`), login,
`copy_increment`. Behind nginx/Render/Railway the IP is your proxy's. On South African mobile
(CGNAT) thousands of users share one IP. Launch on TikTok and signup #11 gets
*"Too many signups from this network."* — for the whole country.

**Fix:** trust `X-Forwarded-For` only from known proxy CIDRs, key on `user` once authenticated,
raise signup to ~30/h, and return `Retry-After` with a "try again in …" page instead of a bare 429.

### 1.7 The economy dead-ends (P1)
Stars are zero-sum: the only inflows are the 5★ welcome grant, being traded, tips, and challenge
bounties. A new user spends 5★ on two vibes and is permanently broke with no way to earn —
and the advertised cash-out needs **500★ = R50** (`users/models.py:697-699`), i.e. 100 trades at
5★. A creator who does that arithmetic stops believing the pitch.

**Fix:** keep most vibes free (they already are), make the *scarce* thing attention not access,
and publish the real path to money: tips + bounties + featured slots. Consider paying a small
bounty for the first published vibe (marketing spend you control) so every new creator's wallet
is never zero.

### 1.8 Everything else that makes it feel empty (P1/P2)
- **No thumbnails.** `thumbnail` is optional (`gallery/forms.py:29`) and the seed sets none, so
  every card is a CSS mock (`templates/gallery/feed.html:368`). Auto-screenshot the sandboxed
  preview into a card image at scan time — that single change transforms the grid.
- **Cards have no date, no comment count, no "updated".** Add "2h ago" + comment count.
- **Battle contradicts itself**: header `+1 ★` (`templates/gallery/battle.html:28`) vs vote
  message "project stars are unchanged" (`gallery/views_community.py:558`). It also creates a DB
  row on a GET (`views_community.py:483`) and uses `order_by('?')` — a full random sort that will
  fall over at scale. Make the stakes real (winner gets a featured slot / badge) or retire it.
- **Challenges tab is a promise with no content** (`templates/gallery/challenge_list.html:5-6`).
  Ship one live challenge with a small bounty, even manually, before launch.
- **Grammar/consistency**: "No live preview for a api / backend" (article bug), the footer
  "Install BlaqVibes" block renders on every page.
- **Trust badge resets on every edit** (`gallery/views.py:893`). Correct, but the creator
  experiences it as punishment. Say it on the edit screen: "✓ comes back after the re-scan".

---

## 2. Ten ways people "hack" it **by accident**

No malice, no DevTools, no scripting — just normal people doing normal things. Each one produces
a broken state, a lost upload, lost money, or a hole in the economy.

| # | Innocent action | What actually happens | Evidence | Fix |
|---|---|---|---|---|
| 1 | **Fix a typo in a published snippet** (title, description, one line of code) | Vibe flips to `pending` and is **404 for the whole world**, while the owner is told *"✓ Vibe updated!"*. No task, no notification, no moderator ticket. It never comes back. | `views.py:898`, `views.py:928`, `access.py:18-30` | Rescan a shadow copy; only the pipeline may move content out of `published` |
| 2 | **Publish your very first vibe (from Studio)** | Needs ≥3 already-published vibes to auto-publish → stays `pending` forever. The flash message promises "we'll tell you when it's uploaded"; nothing ever tells them. | `views.py:671` | Auto-publish clean snippets; real review queue + ticket for the rest |
| 3 | **Upload the folder you actually have** (`node_modules/`, `.env`, `.venv`) | Hard reject with two errors, one of which accuses `node_modules` of being credentials and the other says "provide a ZIP or snippet" — about the ZIP they just provided. | `validators.py:24-32`, `:69`, `:104` | Pre-flight + auto-strip with a receipt, or one actionable error that names the fix |
| 4 | **Self-host it without ClamAV / without a worker** | Every ZIP upload is held for a human who never comes; with Redis down the upload request **hangs ~20 s** and then lies ("You're #1 in line"). | `tasks.py:186-196`, `:294-296`, `views.py:648` | Broker/AV health in `/readyz`, loud failure, stale-job watchdog, explicit AV-disabled policy |
| 5 | **`git push` a one-line fix to a live app** | `status='pending'` → the app 404s for everyone until the rescan finishes (forever, without ClamAV). Worse: **people who already paid lose their download** while it's pending, because `user_can_download` returns False for any non-published status. | `git_daemon.py:664-668`, `access.py:79` | Keep the last-good bytes downloadable during a rescan; buyers' receipts never depend on a scan state |
| 6 | **Type an extra digit in the price box** | `star_cost` / `price_zar` have **no server-side cap** — the `max=5` is HTML only. Stored verbatim: I published a vibe at **9999 ★ / R999999**. The card then advertises a price nobody can pay, and nobody can buy it. | `models.py:65`, `forms.py:36` | `MinValueValidator/MaxValueValidator` (★ 0–5, R0–999) + a confirm step when the price changes |
| 7 | **Deploy without SMTP configured** | Verification mail silently goes to stdout (`fail_silently=True`), so nobody is ever verified → no 5★ → trading and tipping both refuse. The money system is off for everyone and nothing reports it. | `settings.py:583`, `users/views.py:628`, `wallet.py:29`, `economy.py:126` | `security_check` ERRORs on console email in production; ops dashboard counters |
| 8 | **Ten people sign up from one campus / one mobile tower / one proxy** | IP-keyed limiters share one bucket: **signup 10/h**, Nolo 20/h, login 20/m. Person #11 gets *"Too many signups from this network."* — on launch day, for the entire network. | `views.py:84`, `views_community.py:63`, no XFF config | Proxy-aware IP, `key='user'` when signed in, higher ceilings, `Retry-After` page |
| 9 | **Fork something you paid for, then "fix it up" and publish** | The fork is created `pending` with `star_cost=0` and you're redirected straight into the edit page — i.e. into trap #1. Publishing it makes the paid code free for everyone, which most people would not recognise as a problem. | `views.py:1208-1210`, `views.py:1242` | Forks of paid vibes inherit the price by default; warn on publish: "the original is paid — keep a price or get written permission" |
| 10 | **Write a tutorial / demo that *mentions* secrets** (an AWS-key example, a secret-scanner app, a "how to rotate keys" README) | `SECRET_PATTERNS` matches `AKIA…`, `sk_live_…`, `ghp_…`, `-----BEGIN PRIVATE KEY-----` anywhere in the text, so the ZIP is **held as a leaked-secret risk** and never publishes. From the creator's side: "my tutorial got quarantined for no reason". | `validators.py:27-37`, `tasks.py:203-224` | Distinguish *documentation* matches from real keys (entropy + context + filename), add an owner-facing "this is why → contest it" button, and route holds to a human queue with an SLA |

**Honourable mentions** (same family, smaller blast radius):
- Rename (100 ★) and name restyle (20 ★) have no preview of the cost before you commit.
- Holding stars for a payout removes spendable balance; below 500★ you can neither spend nor cash out.
- Deleting an account hands sold vibes to a ghost user — correct, but the creator isn't told that buyers keep access.
- `git push` and every edit reset the 🛡️ trust badge; creators read it as a demotion.

---

## 3. What to do, in order

**This week (stop the bleeding)**
1. Never unpublish on edit / push — scan a copy, flip status only on a decision. *(fixes #1, #5, half of #4)*
2. Delete the `>= 3 published` gate; auto-publish clean snippets. *(fixes #2)*
3. Cap `star_cost` ≤ 5 and `price_zar` ≤ 999 server-side. *(fixes #6)*
4. `security_check` fails when production runs console email or no broker. *(fixes #4, #7)*
5. One honest error for bad ZIPs, with the fix in it. *(fixes #3)*

**Next 2–4 weeks (make it feel alive)**
6. Auto-thumbnails from the sandboxed preview (biggest visual win per hour of work).
7. Card metadata: relative time + comment count + "updated".
8. Seed **one real live challenge** with a bounty; make Battle's reward real or remove the `+1 ★` lie.
9. Pending-state page that tells the truth: queue position, what's being checked, when to expect
   a human, and a "nudge a moderator" button.
10. Proxy-aware rate limits + `Retry-After`.

**Then (make it a habit)**
11. Weekly digest email/notification: "3 new vibes in games · 1 trade on your vibe".
12. Publish the path to money in plain numbers (tips, bounties, cash-out at 500★) on the creator dashboard.
13. First-publish bounty so no wallet is ever permanently zero.

---

## 4. What's genuinely good — don't throw it away

- **The trust pipeline is real evidence, not decoration** (`gallery/trust.py`, only the pipeline
  can write `AppProject.trust`). That is a defensible moat for an AI-generated-code marketplace.
- **The slopsquatting check** (`gallery/dep_check.py`) is a genuinely clever, real answer to a real
  attack, and it fails *open* so it never smears a creator.
- **Honesty as a product decision**: "Preview is not hosting", "no live preview" instead of fake
  chrome, the removed Deploy model. Rare and trustworthy.
- **The ledger discipline** (`users/wallet.py`, `gallery/economy.py`) — zero-sum, idempotent
  welcome grant, largest-remainder splits. The money code is the most careful code here.
- **Studio / "NO BLANK PAGE"** (`gallery/views_community.py:180`) is the right onboarding idea in
  the world; it just needs to end somewhere.
- **The voice.** "Publish the vibes. Clone the culture." / MyVibe Vol.1 → Vol.2. Keep it.

---

## Appendix — reproduce everything

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
DEBUG=1 DJANGO_LOCAL_DEV=1 SECRET_KEY=dev python manage.py migrate
DEBUG=1 DJANGO_LOCAL_DEV=1 SECRET_KEY=dev python manage.py seed_demo
DEBUG=1 DJANGO_LOCAL_DEV=1 SECRET_KEY=dev python manage.py runserver 0.0.0.0:8000
# demo logins: blaq / blaq12345 (admin) · nolo.ai / blaq12345 · thando / thando12345

DJANGO_LOCAL_DEV=1 DJANGO_TEST=1 python manage.py test gallery users   # 662 tests, 1 failure
```
The one failure is `test_login_post_is_rate_limited_at_20_per_minute` (expects 403, gets 200) —
a ratelimit-cache artefact in the test run, not a product bug, but worth a look while you're in §1.6.

To see P0 #1 and #2 with your own eyes: sign in as a fresh user, publish a snippet from
`/studio/`, watch it not appear; then edit its title and watch it disappear.

---

## Appendix B — status at the end of this pass (2026-09-03)

Everything above was the *diagnosis*. This is what the pass actually changed,
with the file that carries it:

| # | Item in this doc | State | Where |
|---|------------------|-------|-------|
| 1 | Edit silently unpublishes (§1.1) | **Fixed** — a rescan is only queued when executable content really changed | `gallery/views.py::_content_fields_changed`, `gallery/views.py::edit_vibe` |
| 2 | First publish never goes live (§1.2) | **Fixed** — snippet and ZIP paths both set the published state | `gallery/views.py::publish`, `gallery/tasks.py` |
| 3 | Beginner ZIP cannot be uploaded (§1.3) | **Fixed** — per-file validation with actionable copy (`.env` → "rename to .env.example", `node_modules/` → "delete the folder") | `gallery/validators.py` |
| 4 | No ClamAV/Celery = silent nothing (§1.4) | **Fixed** — the queue states its reason and the scan status page is honest | `gallery/tasks.py`, `gallery/views.py::scan_status` |
| 5 | Verification email never arrives (§1.5) | **Fixed** — bypass/verification paths plus a 5 ★ welcome grant bound to a verified mailbox | `users/views.py::verify_email`, `users/wallet.py::grant_welcome_stars` |
| 6 | IP-keyed rate limits (§1.6) | **Fixed** — write endpoints keyed per user; the flaky login test now uses a hermetic locmem cache | `gallery/test_security_regressions.py::AuthRateLimitRegressionTests` |
| 7 | Economy dead-ends (§1.7) | **Improved** — payouts + earnings dashboard exist; see also the XP/level/badge loop | `users/payouts.py`, `users/progress.py` |
| 8 | "Nothing happens" (§1.8) | **Fixed by the retention loop** — daily challenge, trending/remix rails, follows + following tab, creator analytics, XP/levels/badges, notification prefs | `gallery/daily.py`, `gallery/trending.py`, `gallery/analytics.py`, `users/progress.py` |

The suite at the end of the pass: **780 tests, 0 failures** (was 662/1 at the
time of writing §Appendix).

New bugs found *by* the new tests, and fixed:

1. `_notify_project_owner` and `_content_fields_changed` had been defined
   directly under a stray `@require_POST` / `@login_required` left over from
   the view above — every star, comment, review and trade raised
   `AttributeError: 'User' object has no attribute 'method'`. Fixed by
   removing the decorators; covered by
   `gallery/test_engagement.py::NotificationSurfaceTests`.
2. `create_pr` swallowed its own `Http404` in a broad `except Exception` and
   answered 302 for a fork that was not the caller's. Now 404s.
3. The AI-README endpoint called a hosted LLM with no rate limit at all;
   payout, review, bookmark and PR-action had none either. All five are now
   bounded (`gallery/test_security_scenarios.py::AntiAbuseRateLimitTests`).
4. `_sparkbars.html` used a multi-line `{# … #}` comment — Django cannot
   strip those, so the comment was being rendered into the page. Now
   `{% comment %}`, with a test that no `{#` reaches the HTML.

Remaining known gaps are listed in the final report under "F".
