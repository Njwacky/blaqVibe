# Account Quarantine & Appeals — Spec

**One line:** when someone breaks a public rule (offensive language first, and
anything else staff decide), their ACCOUNT is held for 30 days, they are told
why and how to appeal, and staff see the appeal and decide.

This is deliberately different from the two things that already existed:

| Thing | Scope | Who is told |
|---|---|---|
| `AppProject.status = 'quarantined'` | one vibe (virus / secrets) | the owner, to fix the bytes |
| `ScanJob.status = 'quarantined'` | one upload | the owner |
| **`UserQuarantine`** | **one account** (a people decision) | the person, in line + inbox + email |

## Flow

```
person posts text
   └─ profanity gate refuses (gallery.profanity.PublicLanguageError)
        └─ view calls users.quarantine.note_blocked_language(request, surface=…)
             ├─ RuleViolation recorded (kind, surface, evidence, project)
             ├─ policy: QUARANTINE_STRIKES (default 1) violations in 90 days
             │     → quarantine_user(): 30-day hold (QUARANTINE_DAYS)
             ├─ person: in-line message + inbox Notification + email
             ├─ staff: in-app Notification + email (all moderators/admins)
             └─ /quarantine/ shows the reason, countdown, evidence, appeal box
                   └─ appeal → staff /moderation/appeals/
                         ├─ accept → hold lifted now, person told
                         ├─ deny   → hold stands, person told why
                         └─ extend → +30 days, person told
```

## What a hold does

* **Blocks** new public content: publish, comment, review, pull request, skill,
  profile text/username, AI README apply, `git push`.
* **Does not block** reading, downloading, trading, spending stars, reporting,
  or deleting the account. A pause on posting is not a deletion.
* **Ends by itself** when `ends_at` passes — `active_quarantine()` reads the
  clock, so no cron is required. `sweep_expired()` (run by the staff queue and
  the person's notice page) tidies the status and tells the person it is over.

## Rules of the road

* Defaults: **30 days**, **1 strike**, **90-day** strike window, one open
  appeal at a time, 24-hour cooling-off between appeals, 30-minute duplicate
  window. Settings overrides: `QUARANTINE_DAYS`, `QUARANTINE_STRIKES`.
* A breach while already held is **recorded, not stacked** — one end date the
  person can understand. Staff can extend deliberately.
* Evidence (`RuleViolation.evidence`) is staff-only and is **never copied into
  notifications or emails** — nobody needs the slur in their inbox, and a
  regression test pins that. An appeal's own words are shown verbatim on the
  appeals page (staff read the real thing) but are replaced by a placeholder in
  the staff email.
* The language gate answers in three states (`gallery.profanity.scan_public_text`):
  `clean`, `blocked` (the words are abusive — recorded, and the author is told)
  and `unavailable` (the matcher itself failed). Both non-clean states refuse
  the text, so nothing unread ever publishes; only `blocked` may count against
  the author, so a bug in our matcher can never quarantine anybody.
* Obfuscation is the same rule as the plain word: leet (`sh1t`), spaced letters
  (`f u c k`), homoglyphs (`fuсk`) and masking (`f**k`, `sh**`, `a**hole`,
  `n****`) all resolve. Masking is token-local, so a masked innocent word
  (`cl*ss`, `grade A*B`) stays clean. The one chosen limit: three or more
  letters erased *inside* a word (`n****r`) is not enough evidence to hold
  somebody — pinned by a test so it stays a decision, not an accident.
* Every staff decision writes an `AdminLog` row (`quarantine_user`,
  `quarantine_lifted`, `quarantine_extended`, `quarantine_appeal`).

## Where things live

| Piece | File |
|---|---|
| Engine (only writer) | `users/quarantine.py` |
| Models | `users/models.py` — `RuleViolation`, `UserQuarantine`, `QuarantineAppeal` |
| Person's page | `users/quarantine_views.py` → `/quarantine/` (`quarantine_notice`) |
| Block decorator | `users/decorators.py` — `not_quarantined` |
| Staff queue | `gallery/moderation.py` — `appeals_queue`, `appeal_action`, `quarantine_action`, `quarantine_new` |
| Staff URLs | `/moderation/appeals/`, `/moderation/appeals/<id>/`, `/moderation/quarantines/new/`, `/moderation/quarantines/<id>/` |
| Staff surfaces | moderation queue link/badge, base nav badge, admin dashboard cards + recent appeals, in-app + email fan-out |
| Templates | `templates/users/quarantine.html`, `templates/gallery/appeals_queue.html`, `templates/emails/{quarantine_notice,admin_user_quarantine,admin_appeal}.{txt,html}` |
| Migrations | `users/0026_*`, `gallery/0044_alter_notification_kind` |

## Wiring a new public write path

1. Add `@not_quarantined` (outermost) to the view — the write is refused for a
   held account, with a message that names the date and the appeal page.
2. If the view can refuse text for language, call
   `note_blocked_language(request, surface='…', form=…, text=…, project=…)`
   exactly where the refusal happens. That is the ONE hook: it records, tells
   the person, holds the account, and notifies staff. A form path must raise
   `PublicLanguageError` (via `validate_public_text`) so the hook can read the
   verdict; a raw-string path passes `text=` and that text is treated as
   `blocked`.
3. Public text that is not a form field (a skill, a changelog, a profile URL)
   still needs step 2 — the rule is about public text, not about forms.

## Tests

`users/test_quarantine.py` — 32 tests covering the breach→notice→hold→appeal→
decision path, read-vs-post enforcement, clock expiry, no-stacking, dedupe,
the raised-threshold warning and its countdown, staff-applied holds, staff-only
access, the banner, the notice page, the dashboard counts, obfuscated abuse,
the skill and profile-URL gates, the 1..365-day clamp, and the rule that an
appeal's profanity reaches staff eyes but never staff inboxes.
`gallery/test_profanity.py` — 30 tests for the matcher itself, including the
masking cases and the three-state verdict.
