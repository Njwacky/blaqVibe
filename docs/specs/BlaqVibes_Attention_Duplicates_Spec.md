# Attention Cases — Duplicates, Malfunctions & Severity-Coloured Notifications — Spec

**One line:** when two of your builds are the same build, or one of them is
provably broken, BlaqVibes tells you in a colour you can read at a glance,
nudges you every 30 minutes, and after 7 days decides for you — writing down
exactly why, parking the loser instead of deleting it, and handing you a FINAL
DELETE button.

## The problem this solves

Two facts about a builder's own workshop used to be invisible:

| Fact | What it cost before | Who paid |
|---|---|---|
| Two of YOUR builds are the same build (`final`, `final-v2`, the same ZIP twice) | Two identical cards in the feed, two detail pages, no way to tell which is real | every visitor |
| One of your builds is broken in a way the platform can PROVE (quarantined bytes, failed scan, preview pointing at a missing file, stuck in the queue, published with nothing to run) | It sat in the workshop looking like progress | the builder, and anyone who opened it |

Neither is the platform's call to make silently, and neither is the owner's call
to make if nobody tells them. So the platform tells them, on a clock.

## Flow

```
upload / git push / hourly sweep
   └─ gallery.attention.detect_for_user(owner)
        ├─ duplicates: fingerprint every build, compare only pairs that CAN qualify
        │    └─ score ≥ ATTENTION_DUPLICATE_THRESHOLD (70) → AttentionCase(kind='duplicate')
        ├─ malfunctions: quarantined | scan failed | no ZIP for a static preview |
        │    preview entry missing | stuck pending 48h+ | published with nothing to run
        │    └─ AttentionCase(kind='malfunction', code=…)
        └─ one pinned Notification per case, category = the case's severity
             ├─ banner on every page (context_processors → attention summary)
             ├─ inbox row sorted critical-first, striped on the left border
             └─ every ATTENTION_REMINDER_MINUTES (30): the SAME row is bumped
                  (is_read=False, reminded_at=now, remind_count+1)

owner answers at /attention/
   ├─ KEEP THIS ONE          → decision_source='user',    loser parked, 24h clock starts now
   ├─ LET BLAQVIBES DECIDE   → decision_source='system',  rationale written, clock waits to be seen
   ├─ KEEP BOTH / I'LL FIX IT→ status='dismissed',        never re-asked (pair_key is UNIQUE)
   └─ nothing for 7 days     → expire_due() picks the keeper, same code path as the button

decided
   ├─ FINAL DELETE  → lifecycle.remove_project (money-aware hard delete)
   ├─ UNDO          → lifecycle.restore_project at the status it had before the park
   ├─ LET ME CHOOSE → reopen(): everything restored, a fresh 7-day window
   └─ 24h of silence AFTER the owner opened it → delete_due() erases the parked copy
```

## Severity: five colours, on the side of the border

The job of the colour is to answer one question before a word is read: **what do
I deal with first?** So there are five categories, not one per notification kind
— twenty-eight kinds would give twenty-eight colours and no ordering at all.

| Category | Stripe (dark / light) | Meaning | Kinds |
|---|---|---|---|
| `critical` | `#F43F5E` / `#DC2626` | Blocked, unsafe, or about to be erased | quarantined, account_quarantine, appeal, git_push_rejected |
| `action` | `#F59E0B` / `#D97706` | A decision is waiting, with a deadline | duplicate, malfunction, approval, pending, review, review_needed, challenge_draft, pr, co_owner, report |
| `money` | `#10B981` / `#059669` | Stars or Rand moved | tip, trade, sale, payout |
| `social` | `#38BDF8` / `#0284C7` | Somebody responded to your work | comment, follow, star, fork, challenge |
| `system` | `#8B8BA3` / `#6B7280` | Information, nothing to do | published, upload, git_push, milestone, achievement, role |

Rules that make it more than decoration:

* **The colour is stored, not derived at render time.** `Notification.category`
  is written by `notify()` from `CATEGORY_OF_KIND`. A row keeps the colour the
  person already saw even if the map changes later.
* **The inbox sorts on it.** `notify.inbox_queryset` orders by
  `severity_rank`, then by recency — a quarantined build from yesterday outranks
  a star from a minute ago.
* **The colour is never the only channel.** Each stripe is paired with the
  category WORD in the same colour, plus a legend row that says what each one
  means. Colour alone is not an interface.
* **An unmapped kind is a test failure**, not a silent fallback to grey.
  `test_every_kind_has_a_category` pins it, because grey means "nothing to do"
  and would hide a new urgent kind at the bottom of the inbox.
* **Light mode gets darker stripes**, not the same hex on white. "Critical"
  must not turn pink in daylight.

## The keeper strategy — the "strategic way" the system explains itself

When BlaqVibes chooses, it does not shrug: it scores every copy against a
printed rule and writes the winning reasons into `AttentionCase.rationale`,
shown verbatim on the case page and summarized in the notification.

```
 1. Never delete a receipt — a copy somebody paid for is untouchable.   1000
 2. Keep the published copy over the one still in review.                120
 3. Keep the copy that passed the scan (verified > scanned > unknown).   40 / 20
 4. Keep the copy with the real artifact attached (a ZIP).                25
 5. Keep the copy you wrote proof for (problem / what you did / AI fail). 25 each
 6. Keep the copy other builders remixed — deleting orphans lineage.      30 × ≤3
 7. Then reviews, stars, views — CAPPED.                        33 points total
 8. Then the copy you were still working on, then the original upload.    25 / 12
```

Point 7 is the one that keeps this honest against the product standard
("followers and stars do not decide that ranking"): `MAX_POPULARITY` — every
popularity signal summed at its cap — is 33, which is less than one published
copy (120), less than two proof fields (50), and less than one remix child's
lineage (30). Popularity can tie-break between two otherwise identical copies;
it can never outvote evidence. `test_popularity_can_never_outvote_evidence`
pins the arithmetic so a future weight edit cannot turn that sentence into a
slogan.

Point 8's first half is a **comparison, not a calendar check**. Both halves of a
duplicate were usually touched this month, so paying both of them for recency
would tell the owner that each copy is "the one you were still working on" —
the same claim twice, deciding nothing. Among peers only the freshest touch
earns the 25 points (`RECENT_WINDOW_DAYS = 30`; equal timestamps fall back to
the lowest id). A malfunction scores one build with no peers, so there the
window itself is still the question.

Tie-breaks are deterministic (score → earliest upload → lowest id), because two
runs over the same facts must produce the same keeper or the "why" is fiction.

For a **malfunction** the question is not "which copy" but "is this worth
fixing?" — `malfunction_verdict()` returns `keep` when something real depends on
the build (a receipt, a remix child, reviews/stars, it is live on the feed) and
`drop` when nothing does, with the score and the reason printed either way.

## The countdown, and what each silence means

| Moment | Setting (default) | What happens |
|---|---|---|
| Case opens | — | Notification + banner + inbox row, striped by severity |
| Every nudge | `ATTENTION_REMINDER_MINUTES` (30) | The SAME row is bumped unread; banner re-appears even if it was snoozed; an open tab re-nags via `/attention/status/` |
| Deadline to choose | `ATTENTION_DECISION_DAYS` (7) | `expire_due()` picks the keeper, writes the rationale, parks the loser |
| Owner opens a system decision | — | `acknowledge()` starts the FINAL DELETE clock |
| Silence after opening it | `ATTENTION_FINAL_DELETE_HOURS` (24) | `delete_due()` erases the parked copy |
| Decision never opened at all | `ATTENTION_UNOPENED_BACKSTOP_DAYS` (7) | Same erase, on a longer fuse — nobody saw it, so it gets more benefit of the doubt |

Two silences are treated differently on purpose. Silence **after** the owner has
demonstrably opened the decision is consent — they read why, they saw the
button, they walked away. Silence from somebody who never opened anything is not
consent, so it gets a longer backstop and the erase is still money-aware.

Reminders stop the moment the owner has seen a decision: from then on a visible
countdown is running, and another unread bump every half hour would be noise on
top of a deadline they already know about.

## Nothing is destroyed quietly

* **Parking is soft.** `lifecycle.park_project` sets `status='removed'` — off the
  feed, search and detail page; buyers keep their download; restorable. It is a
  separate function from `remove_project` precisely because a machine decision
  must be reversible and an owner's own delete may be hard.
* **FINAL DELETE is the owner's click**, and it goes through
  `lifecycle.remove_project`, which is money-aware: if anybody ever traded stars
  or paid Rand, the erase stops at `removed` and the owner is told why
  ("somebody paid for it, and their download is their receipt"). The reason is
  recorded on the candidate (`snapshot.cannot_erase`) so the button reads as
  honest rather than broken.
* **UNDO puts it back at the status it had before the park**, from
  `snapshot.status_before_park` — restoring a build that was pending scan must
  not silently publish it.
* **LET ME CHOOSE** (`reopen()`) restores every parked copy before asking again,
  because a choice between a live build and an erased one is not a choice.
* **The record survives the erasure.** `AttentionCandidate.project` is
  `SET_NULL` and `snapshot` keeps the title, upload date, status, file count,
  stars, views, reviews and paid counts. The case still reads afterwards.

## Detection, and what it deliberately ignores

Fingerprints are deterministic and read stored columns only — no LLM, no
network, no unzipping anything inside a request:

| Signal | Points | Why |
|---|---|---|
| Same file list (≥2 paths) | 45 | The archive itself |
| Same file list (1 path) | 20 | `index.html` is everybody's first file |
| Byte-identical inline code | 45 | The build itself |
| Same normalized title | 25 | `My  Dashboard!!` = `my dashboard` |
| Slug differs only by a `-2` suffix | 10 | The platform's own collision marker |
| Same language mix | 8 | Corroboration |

Threshold 70 (`ATTENTION_DUPLICATE_THRESHOLD`).

Never a case:

* **A remix of the other build, or two remixes of the same parent.** Lineage is
  credit (README). Flagging it would teach builders that remixing gets their
  work deleted.
* **Another owner's similar build.** That is a plagiarism/copyright question for
  the report + moderation flow, where a human moderator decides — not a timer
  that deletes somebody's work because a stranger uploaded similar bytes.
* **A build already parked** (`status='removed'`) — it is off the public site, so
  it cannot be half of a live duplicate.
* **A problem already answered.** `pair_key` (sha256 of user + kind + problem
  identity) is UNIQUE, so an hourly sweep can never re-ask a question the owner
  already dismissed. A false positive answered twice is worse than one answered
  once.

### Why detection is O(n) and not O(n²)

The weak signals add to `WEAK_SIGNAL_MAX` = 43, below the default threshold of
70. So a pair that matches **neither** the file list nor the code cannot qualify,
and bucketing on those two hashes is *exact* — the same answers as comparing
every pair. That matters because detection runs on a page load: `/attention/`
detects before it claims "nothing is waiting on you", and a builder with 300
uploads would otherwise pay 45,000 comparisons per visit. If an operator lowers
the threshold below 43, `_pairs_worth_comparing` notices and compares everything
instead. `test_weak_signals_alone_cannot_reach_the_threshold` pins the
arithmetic so the bucketing cannot silently start missing duplicates.

## Where things live

| Piece | File |
|---|---|
| Engine (only writer) | `gallery/attention.py` |
| Models | `gallery/models.py` — `AttentionCase`, `AttentionCandidate`, `Notification.category/reminded_at/attention_case` |
| Severity table + inbox ordering | `gallery/notify.py` — `CATEGORY_META`, `CATEGORY_OF_KIND`, `inbox_queryset`, `redeliver` |
| Park / restore / erase | `gallery/lifecycle.py` — `park_project`, `restore_project`, `remove_project` |
| Views | `gallery/views_attention.py` → `/attention/`, `/attention/status/`, `/attention/<id>/…` |
| Inbox view (severity-ordered) | `gallery/views.py` — `notifications_inbox`, `notifications_mark_all_read` |
| Banner on every page | `gallery/context_processors.py` + `templates/gallery/includes/attention_banner.html` |
| Templates | `templates/gallery/attention.html`, `attention_case.html`, `includes/_attention_case.html`, `notifications.html` |
| Stripe + case styling | `static/gallery/css/attention.css` |
| 30-minute re-nag in an open tab | `static/gallery/js/attention.js` |
| Celery | `gallery/tasks.py` — `attention_check` (chained after upload), `attention_sweep`, `attention_reminders` |
| Ops command | `python manage.py attention_sweep [--dry-run] [--only detect|expire|remind|erase] [--user NAME]` |
| Tests | `gallery/test_attention.py` (104 tests) |

## Rules of the road

* **One pinned notification per case, ever.** A reminder bumps it
  (`redeliver()`) instead of appending, so a 30-minute cadence for 7 days is 336
  nudges and still one inbox row, one unread badge, one history.
* **"Mark all read" cannot silence a countdown.** Rows whose case is still
  `open`/`decided` are held unread; the endpoint reports how many it held.
* **Owner-scoped by queryset.** Every view filters on `user=request.user`, so a
  stranger's case id is a 404 — a 403 would confirm the case exists to somebody
  guessing ids. Nothing destructive answers to GET.
* **`/attention/status/` carries counts and clocks only** — no titles, slugs or
  ids. It is JSON on an authenticated endpoint, written as if somebody hostile
  could read every byte.
* **Staff get visibility, not the pen.** `AttentionCaseAdmin` is read-only: a
  hand-editable `status` or `final_delete_at` would be a way to erase a build
  without the notification that promises it.
* **Detection never fails an upload.** `attention_check` is the last link of the
  upload chain, catches everything, and returns counts.
* **The dry run is the sweep, minus the writing.** `--dry-run` counts through
  `expiry_due_queryset`, `reminder_due_queryset`, `erase_due_queryset`,
  `sweep_owners` and `duplicate_pairs` — the engine's own functions — and skips
  every pair or defect that already has a case, because the engine would skip it
  too. A report that predicted work the sweep will not do would teach operators
  to ignore the report.
* Settings: `ATTENTION_ENABLED`, `ATTENTION_DECISION_DAYS`,
  `ATTENTION_REMINDER_MINUTES`, `ATTENTION_FINAL_DELETE_HOURS`,
  `ATTENTION_UNOPENED_BACKSTOP_DAYS`, `ATTENTION_DUPLICATE_THRESHOLD`,
  `ATTENTION_STUCK_HOURS`, `ATTENTION_DETECT_BATCH`. Every one of them is quoted
  in user-visible copy generated from the same number, so the sentence "you have
  7 days" and the clock that enforces it cannot disagree.
