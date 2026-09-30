# BlaqVibes — Quarantine Clean-up Spec

**Question answered:** as staff with plenty of users, what is the best and
fastest way to delete the quarantined ones — a search bar plus bulk selection,
or whatever developers recommend for this?

**Short version:** search, select, **review**, then confirm — and make one
function the only way an account is removed in bulk.

* `/admin/quarantined/` is a **bounded, searchable list** of the accounts held
  right now. Every row says *Ready to delete* or says **why not**.
* `/admin/quarantined/review/` is a **dry run**: who goes, who is refused and
  why, what it destroys, what it keeps. It changes nothing.
* `/admin/quarantined/delete/` is the **one write**. It needs a reason and a
  typed phrase that carries the count (`DELETE 12`).
* `users/account_cleanup.py` is the **only writer**. Guards, audit rows and the
  notice live there, so a second caller cannot ship a weaker path.

**Scope.** “Quarantined” means three things in this codebase: a *vibe*
(`AppProject.status='quarantined'`), a single *upload* (`ScanJob`), and an
*account* (`UserQuarantine`, see `BlaqVibes_Account_Quarantine_Spec.md`). This
tool is for **accounts**. The “Quarantined — Virus/Secrets” list of vibes in the
moderation queue is untouched (it keeps its per-vibe Delete). The same
pattern — search, select, review, chunked delete — would carry over to that list;
the code here does not.

Who can use it: **Admin and Super Admin** (the same tier that can already delete
any vibe). A moderator can see the holds on the appeals page but not delete them.

---

## 5 Whys — why search + select + review + confirm

The tools that existed were a per-row card on `/moderation/appeals/` (lift /
extend / expire, no delete) and Django admin.

1. **Why not a delete button per row?** Because the cost is per account: a
   click, a confirm dialog, a page reload. A hundred bot accounts is three
   hundred actions, and the operator has stopped reading by the twentieth.
   **Why not Django admin's bulk “delete selected”?** It has no “quarantined”
   filter, it refuses any account that owns a sold vibe (the `PROTECT`
   constraint), and it skips what this app promises around a delete: the audit
   row and the notice.
2. **Why not one “delete all quarantined” button?** Because *quarantined* is a
   state people move out of. Between the click and the commit an appeal
   arrives, another admin lifts a hold, a moderator is held by mistake. A button
   that acts on a **filter** acts on a set the operator never saw. This tool acts
   on the **ids that were reviewed**, and re-checks each one at the moment of
   deletion.
3. **Why a review step?** So the operator sees the blast radius of exactly that
   set: accounts, vibes that die, *sold* vibes that survive, stars destroyed,
   remixes by other builders that lose their “remixed from” link, and the
   accounts that will be skipped, each with its reason. The review is read-only,
   and the ids it shows are the ids the delete receives.
4. **Why a typed phrase *and* a reason?** The phrase carries the number
   (`DELETE 12`), so a click on the wrong page or a stale tab cannot confirm a
   count the operator never saw. The reason is the “why” that `AdminLog` keeps
   after the account itself is gone and can no longer be asked.
5. **Why hard delete, not a “deactivated” flag?** The account being gone *is* the
   goal (spam removed, usernames freed, data erased), and the quarantine that
   precedes it is already the reversible stage: the person was held, told, given
   72+ hours and an appeal form. What must not be destroyed — sold vibes and
   Trade/Sale receipts — is kept by the same mechanism the self-service “Delete
   account” uses (`gallery.lifecycle`).

---

## The flow

```text
/admin/quarantined/            GET   search · reason · show (all/ready/blocked) · sort · per page
  ?q=promo&reason=spam                25 / 50 / 100 rows, server-paged, live-updating
                                      tick rows (selection survives searching and paging)
            │ POST ids
            ▼
/admin/quarantined/review/     POST  dry run — nothing is written
                                      impact · refused-with-reasons · reason + "DELETE n" form
            │ POST ids + reason + confirm (+ notify)
            ▼
/admin/quarantined/delete/     POST  re-check → delete in chunks → audit → notice
            │ 302
            ▼
/admin/quarantined/?cleared=1   flash: deleted / skipped / failed, batch id
```

The list needs no JavaScript: it is a real GET search form and a real POST form.
The script adds live search (debounced, swaps only the results block), a
selection that survives searching, select-all-on-page, shift-click ranges, the
batch-limit cap, and the disabled-until-typed delete button. **None of it is a
security boundary** — the server re-checks the ids, the phrase and the reason.

---

## Guards — one sentence per refusal

Every guard is computed by **one annotated query** (`held_accounts()`), read by
the list page, the review and the delete alike, so “what the page showed” and
“what the delete checks” cannot drift apart.

| Code | Refused when | Why | Clears by |
|---|---|---|---|
| `self` | it is you | use Settings → Delete account; this tool must not become how an admin removes their own access | — |
| `staff` | role moderator / admin / superadmin, or Django `is_staff` / `is_superuser` | changing power is a role change, with its own audit and confirmation | Manage roles |
| `system` | the `ghost` account | it owns every vibe people paid for | never |
| `appeal` | an appeal is **open** | a person is waiting for an answer; deleting them is an answer | decide the appeal |
| `fresh` | the hold is younger than **72 h** (`ACCOUNT_CLEANUP_MIN_HOLD_HOURS`) | the hold may be a false positive, and the person needs time to read the notice and appeal; the row says the exact time it opens | time |
| `payment` | a `PaymentIntent` is pending and younger than **24 h** (`ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS`), as buyer **or** as owner of the vibe being bought | the Paystack webhook returns `unknown reference` for a deleted intent — the customer is charged and nothing is delivered. The window is deliberately longer than the 30-minute `INTENT_TTL`: the webhook never looks at `expires_at`, and Paystack retries a failed live webhook hourly for up to 72 h (96 covers a whole retry window) | time |
| *(not held)* | no live hold at the moment of the delete (lifted, expired, appeal accepted) | back in good standing | — |

A decided appeal (accepted/denied) does not block. `ACCOUNT_CLEANUP_MIN_HOLD_HOURS=0`
switches the window rule off.

Searching by **username** is open to admins. Searching by **email** (including
alternative addresses on the account) is super-admin only, the rule from
`BlaqVibes_Admin_User_Search_Spec.md`; the page never prints an email address
to anyone.

---

## The write path (`delete_quarantined_accounts`)

1. Everything that can be refused **before any write** is refused: not an admin,
   no ids, more than the batch limit, reason under 5 characters, phrase ≠
   `DELETE <count>`.
2. The ids are processed in **chunks of 25**. Each chunk is one transaction:
   1. row-lock the live holds (`select_for_update`) — lifting, extending and
      answering an appeal all write that row;
   2. **re-run the guard query** on the chunk — the review may be minutes old;
   3. `release_accounts_projects()` — sold vibes move to the `ghost` user as
      `removed` (buyers keep their download, receipts intact);
   4. one `User.objects.filter(pk__in=…).delete()` — unsold vibes and all
      personal data cascade;
   5. one `AdminLog` row per account, in the **same** transaction: an audit
      row can never exist for an account that survived, or be missing for one
      that did not.
3. If a chunk fails (a sale landed after step 3 and `PROTECT` caught it, a
   deadlock) it has already rolled back, and its accounts are **retried one at
   a time**. The retry sees the new sale and keeps the vibe. One account that
   keeps failing is reported as failed; the rest of the batch is unaffected.
4. One summary row (`bulk_delete_accounts`, with a batch tag) closes the batch.
5. **After the commit** (never inside it, like `users/roles.py`) an optional
   notice email goes to each deleted person who had an address: the reason
   label, what happened to their vibes, and the operator's contact address if
   one is set in the footer contacts. It never carries the evidence text. It is
   best-effort and **time-boxed to 6 seconds** (`ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS`) — what does not fit is counted
   and reported, never retried silently — and it defaults **off for spam-only
   batches**, because bot addresses bounce and bounces cost the sender
   reputation that sign-in and password-reset mail depend on.

Double-clicking the final button is harmless: the script locks it after one
click, and a second request finds the accounts already gone (“Already deleted.”;
no second per-account audit row — only a batch row recording the attempt).

**What a delete destroys:** sign-in, profile, comments, reviews, PRs, tips,
follows, inbox, the star wallet and ledger, payment intents, and the quarantine
record itself — including the violation evidence and any appeals. The `AdminLog`
line is the only trace, which is why the reason is mandatory.

**What it keeps:** sold vibes (under `ghost`, status `removed`), every
Trade/Sale row (the deleted person becomes `NULL`), other builders' remixes (they
lose `forked_from`), and the audit rows themselves.

---

## Performance — measured, not assumed

| | queries |
|---|---|
| One account, deleted on its own | **97** |
| A chunk of 25 accounts | **88** (3.5 per account) |
| 100 accounts in four chunks | **349** |
| 100 accounts, one at a time | ~9,700 |
| The held list (any number of rows, per page ≤ 100) | **4** (totals, reason counts, count, page) |

(SQLite query counts on this schema; Postgres runs the same statements.) The
cost is a fixed per-chunk probe of every table that points at a user, so it
follows the number of **tables**, not of accounts. On a remote Postgres that is
the difference between a couple of seconds and a blown 30-second request —
which is also why a batch is capped (`ACCOUNT_CLEANUP_MAX_BATCH`, default 100)
instead of offering “select everything that matches”.

**Timings on real PostgreSQL 16** (embedded server over a local socket, so
round-trip latency is ~0; 20,000 users, 2,000 of them held, a third with vibes):

| Step | Time | Queries |
|---|---|---|
| List, page 1 (50 or 100 rows) | 45–55 ms | 4 |
| List, last page of 40 | 74 ms | 4 |
| Search “user_0012”; two words + reason + *ready only* | 41 ms; 61 ms | 4 |
| Sort by most vibes / by name | 42 ms / 44 ms | 4 |
| Review of 100 accounts | 19 ms | 2 |
| **Delete 100 accounts** (102 vibes, 4 chunks, 100 audit rows) | **387 ms** | 357 |

Latency to a managed database multiplies the query counts, not the row counts:
at an assumed 5 ms round trip the 100-account batch is ~1.8 s, while the same
100 accounts one at a time (~9,700 queries) would be ~48 s — past a 30-second
worker timeout. That is the case for chunked deletes in one line.

* The list does no per-row work: username, hold, vibe and sold counts, stars,
  and every blocker come from the one query. A test pins that 5 rows and 60
  rows cost the same number of queries. (The shared `_avatar.html` evaluates
  the creator-rank frame for every row — two aggregate queries each — so this
  page draws its own avatar.)
* `filter(Exists(live hold))` runs first, so the table of tens of thousands of
  users is narrowed to the handful that are held before any subquery runs.
* **Caching.** The list answers one URL in two shapes (full page, and the bare
  results block for the script), so it sends `Cache-Control: no-store, private`
  and `Vary: X-Requested-With`. Found in a real browser, not a unit test: without
  that, searching, leaving and pressing Back rendered the bare fragment — no nav,
  no styles — because the HTTP cache had stored the script's response under the
  page's URL. A regression test pins the headers.

---

## What every developer recommends — and where this stands

| Practice | Here |
|---|---|
| Search + filters, server-side, paged | ✔ username (email for super admins), reason, ready/blocked, sort, 25/50/100 |
| Bulk select that survives search and paging | ✔ kept per tab, capped at the batch limit, shown as “N selected (M from other pages)” |
| Select-all, shift-click ranges, keyboard | ✔ native checkboxes, labelled for screen readers; blocked rows say why |
| State in the URL | ✔ `?q=…&reason=…` — shareable, survives refresh |
| Preview / dry run of the blast radius | ✔ the review step |
| Typed confirmation with the count | ✔ `DELETE 12` |
| A reason, and an audit trail | ✔ required; one `AdminLog` row per account + a batch row |
| Least privilege | ✔ admin tier; email search super-admin only |
| Re-validate at execution, not just at display | ✔ under lock, per chunk |
| Report per-item results, isolate failures | ✔ deleted / skipped / failed, with reasons |
| Idempotent / double-submit safe | ✔ |
| Rate-limit and CSRF-protect the destructive POST | ✔ 30/h delete, 120/h review (per admin; both are settings) |
| Tell the affected person | ✔ optional email, after commit |
| **“Select all N matching”** across pages | ✘ deliberate: a filter can match something new between click and commit. Bigger backlogs are several rounds of ≤ 100, each reviewed |
| **Soft delete + undo window** | ✘ see below |
| **Background job with progress** | ✘ the work is seconds, not minutes; a job table, a worker queue and a progress page would be more machinery than the problem |
| **Block re-registration** of a removed abuser | ✘ see below |
| **Delete stored files** (ZIPs, thumbnails, avatars) | ✘ see below |
| **Export before delete** | ✘ an export of emails is a leak waiting to happen; the audit line keeps username, reason and counts |

### Reconciling with the role-change spec

`BlaqVibes_Admin_User_Search_Spec.md` rejected two ideas that appear here.

* **Bulk tooling** was rejected for role changes because they are “1–5 events a
  quarter, each with a human reason”. Clearing quarantined accounts is the
  opposite case: a volume action (a bot wave is hundreds at once) whose
  per-item decision is usually identical. The guard rails the role spec was
  protecting do not go missing here — they run **per account** inside the bulk
  path, and refusals are reported per account.
* **Live search** was rejected as “a second thing to secure, rate limit and keep
  in sync with the guards”. Here the live request is the *same view* with the
  *same* `@admin_required` and the same query; it only returns less HTML. The
  no-JS path is a normal GET form.

### Known limits and sensible next steps

1. **There is no undo.** The quarantine is the soft stage; this is the final
   one. If a grace period is wanted, the shape is: a tombstone row
   (`AccountDeletion`: who scheduled it, when, why, purge-after), `is_active=False`
   so sessions die immediately, a restore button for the window, and a
   `purge_scheduled_accounts` management command calling the same
   `delete_quarantined_accounts` guards. That is a migration and a product
   decision about how long personal data may be kept.
2. **A removed person can register again** with the same email. A hashed-email
   tombstone checked at sign-up would stop the lazy case; it needs a line in the
   privacy policy and only slows a determined abuser.
3. **Stored files are not deleted.** Django does not remove `FileField` files
   when a row is deleted, and the self-service “Delete account” and the owner's
   “Delete vibe” have the same gap. A periodic orphan sweep would fix all
   three at once.
4. **Notices are sent inside the request** (6-second budget, a setting). If people-batches
   of 100 with notices become routine, send them from the `scan` Celery queue
   instead; the worker today consumes only that queue.

---

## Security model

One row per thing that can go wrong, what stops it, and the test that fails if
the control is removed (each was also broken on purpose to confirm the test goes
red).

| Threat | Control | Pinned by |
|---|---|---|
| Someone who should not be here | `@admin_required` on all three URLs, and the engine re-checks `is_admin` before it touches anything. Anonymous → sign-in; user/moderator → 403 (logged, and sent to Sentry by the decorator) | `CleanupViewTests` (anonymous, plain user, moderator, admin, super admin) |
| A malicious page making an admin's browser POST | CSRF token in both forms, Django's middleware; GET cannot change anything (`@require_POST`) | `test_csrf_is_enforced_on_the_destructive_post`, `test_review_and_delete_are_post_only` |
| Clickjacking | `X_FRAME_OPTIONS = 'DENY'` site-wide in the production posture; `security_check --strict` (CI gate 1) reports any other value, so the build fails. Set once in settings, not per view | CI gate 1 |
| Admin data left in a cache | `Cache-Control: no-store, private` on the list, the review and the delete; `Vary: X-Requested-With` on the list | `test_review_and_delete_responses_are_never_cached`, `test_the_script_gets_only_the_results_block_and_a_marker` |
| Script injection (XSS) | Every value is auto-escaped; no `\|safe`; no inline script, style or handler in the page templates; the script writes with DOM properties and `textContent`; its one `innerHTML` takes same-origin, server-escaped HTML and only after a marker header proves it is our partial | `test_a_hostile_username_is_escaped_everywhere_it_is_printed` (list, review, flash, audit page), `test_hold_detail_is_escaped`, `test_templates_load_the_external_files_and_carry_no_inline_code` |
| SQL injection | ORM only; `sort`, `show`, `reason`, `per` are whitelists that fall back to defaults; the ORM escapes `%` and `_` in `icontains`, so a wildcard typed into the box matches itself | `test_like_wildcards_typed_into_the_box_are_matched_literally`, `ListingTests` (malformed URLs) |
| Hostile ids | plain ASCII digits only, at most the database's largest id (2³¹−1 on PostgreSQL), unique, at most the batch size; anything else never reaches a query. A 30-digit id used to raise from the driver and surface as a 403 page | `test_ids_are_plain_ascii_digits_inside_the_database_range`, `test_ids_that_can_not_exist_never_reach_a_query_or_crash_a_page` |
| Hostile free text (search, reason) | control, bidi, zero-width, private-use and unassigned characters are removed, whitespace collapsed, length capped. A NUL byte makes PostgreSQL refuse the whole audit row; a right-to-left override makes a line read backwards; a row of zero-width spaces passed the “5 characters” rule while saying nothing | `test_the_reason_is_plain_printable_text_before_it_is_audited`, `test_an_invisible_reason_is_no_reason`, `test_the_search_drops_control_and_invisible_characters`, `test_ordinary_text_passes_through_untouched` |
| Log forging | a username is logged with `%r`, never raw | `test_a_username_cannot_forge_a_log_line` |
| A name the profile URL cannot carry | `{% url … as %}` — the row shows the name unlinked instead of the whole list failing (these are exactly the accounts this page exists to clear) | `test_a_username_that_cannot_be_linked_does_not_take_the_page_down` |
| Tampered browser storage | only plain ids come back out of `sessionStorage`, at most the batch limit; the server re-checks every id anyway | `test_the_script_trusts_only_plain_ids_from_session_storage` (and a real-browser run) |
| Mass deletion by a rogue or hijacked admin session | at most 100 a batch; a review, a typed count and a reason; 30 deletes/h and 120 reviews/h per admin; staff, self and the ghost are refused; every account is re-checked under lock; one audit row per account plus a batch row | `DeleteTests`, `test_the_hourly_rate_limit_…`, `test_the_rates_reach_the_views` |
| Privacy | no email address is ever printed; email search is super-admin only; the notice never carries the evidence; no secret or credential is stored or added | `test_the_page_never_prints_an_email_address`, `test_email_search_is_for_super_admins_only`, `test_the_notice_never_carries_the_evidence_or_a_login_link` |
| A form too big to post | the batch cap (≤ 500) plus the other fields stays under Django's 1,000-field limit | `test_the_largest_allowed_batch_still_posts`, `test_the_batch_cap_fits_inside_djangos_form_field_limit` |
| A mistyped setting | numeric variables are read through `blaqvibes/envparse.py`: a typo stops the boot and names the variable (the host keeps the old release running); a number outside its range is clamped; a rate that django-ratelimit would crash on is refused at start, not at request time | `EnvParseTests`, `SettingsTests` |

**Not built — each is a decision, not an oversight**

* **Re-entering the password** before a delete (step-up auth). No other
  destructive action in the app asks for it — self-service “Delete account” and
  role changes use a typed confirmation, as this page does — so the typed count
  and the reason are the friction here. Worth adding if admin sessions are ever
  shared or left open.
* **A second approver**, or an alert to super admins when a batch is large.
  The audit page and `bulk_delete_accounts` rows are the trail today.
* **A site-wide Content-Security-Policy.** The base template still has inline
  styles and scripts, so a strict policy would break other pages. These three
  pages carry none, so they would pass one unchanged.
* **Append-only audit rows in Django admin.** `AdminLogAdmin` blocks add and
  change but not delete, so a Django superuser can erase audit rows (the star
  ledger next to it is fully append-only). Adding
  `has_delete_permission → False` would close it; it is left as it was because
  it changes shared admin behaviour and someone may purge old rows there.
* **The shared mail helper logs the recipient's address** at INFO
  (`send_generic_email`). It does so for every email in the app, including these
  notices.

## Settings

Six settings, each in `blaqvibes/settings.py` and `.env.example`. A value that is
not a whole number (or a rate not shaped like `30/h`) **stops the boot** and names
the variable; a number outside its range is **clamped**.

| Setting (env) | Default | Range | Meaning |
|---|---|---|---|
| `ACCOUNT_CLEANUP_MIN_HOLD_HOURS` | `72` | 0–720 | hours a hold must have run before its account can be deleted; `0` turns the rule off |
| `ACCOUNT_CLEANUP_MAX_BATCH` | `100` | 1–500 | accounts per confirmed delete. 500 is the ceiling because every id is one POST field and Django refuses more than 1,000 |
| `ACCOUNT_CLEANUP_PAYMENT_HOLD_HOURS` | `24` | 1–720 | how long a pending checkout keeps its buyer and its seller undeletable. Never 0: that would switch off the guard that protects customers' money |
| `ACCOUNT_CLEANUP_NOTICE_BUDGET_SECONDS` | `6` | 1–20 | total time the notice emails may take inside the request. 20 because gunicorn's default worker timeout is 30 s and one email can take `BREVO_TIMEOUT` (10 s) on top |
| `ACCOUNT_CLEANUP_REVIEW_RATE` | `120/h` | `count/[multiple]s·m·h·d` | per-admin limit on the review step |
| `ACCOUNT_CLEANUP_DELETE_RATE` | `30/h` | `count/[multiple]s·m·h·d` | per-admin limit on the delete step (`5/m`, `10/2h` …). Read on every request, so an override applies at once |

The hold window, batch limit and payment window are quoted in the page copy, so
the sentence and the rule are one number.

**Fixed on purpose** (constants in `users/account_cleanup.py`, not settings):
`CHUNK_SIZE = 25` (a measured trade-off between query count and lock time),
`PAGE_SIZES = (25, 50, 100)` (the choices the page offers), the reason length
5–100 (tied to `AdminLog.target`, a 200-character column). Which roles count as
staff is not a constant at all: it is every role above `user` in
`users/roles.ROLE_ORDER`, so a role added there later is protected by default.

## Files

| File | What |
|---|---|
| `users/account_cleanup.py` | `held_accounts()` (the one guard query), `listing()`, `review()`, `delete_quarantined_accounts()` — the only writer |
| `gallery/lifecycle.py` | `release_accounts_projects()` (batch) and `paid_projects_of()`; `release_account_projects()` now delegates to it |
| `users/admin_views.py` | `quarantined_accounts`, `quarantined_review`, `quarantined_delete`; `audit_log` is now paged |
| `users/urls.py` | `/admin/quarantined/`, `…/review/`, `…/delete/` |
| `templates/users/quarantined_accounts.html`, `_quarantined_results.html`, `quarantined_review.html` | the page, the results block (also what live search swaps in), the dry run + confirm form |
| `templates/emails/account_removed.{txt,html}` | the notice |
| `static/gallery/css/quarantine-cleanup.css`, `static/gallery/js/quarantine-cleanup.js` | styles (stacks below 1080 px) and behaviour; no inline CSS/JS |
| `templates/gallery/appeals_queue.html`, `templates/users/admin_dashboard.html` | the links (admins only on the appeals page) |
| `users/roles.py` | the Admin role description now mentions the tool |
| `blaqvibes/settings.py`, `.env.example` | the six settings |
| `blaqvibes/envparse.py` | reads numeric/rate environment variables: clamps ranges, refuses junk by name |
| `users/test_quarantine_cleanup.py` | the suite (see below) |

## Test gates

```bash
python manage.py test users.test_quarantine_cleanup   # the new suite
python manage.py test gallery users                   # scripts/ci.sh runs this
```

The new test module was also run against PostgreSQL 16 and passes there in full,
as do the neighbouring suites it touches (audit log, dashboard, quarantine, account
delete, mobile layouts). Two older tests in `users/test_role_admin.py` — the ranked-search trigram fallback and
“exact match goes straight to the person” — fail on Postgres and pass on SQLite;
they fail identically on the commit before this work, so they are unrelated.

What the suite pins: every guard and its sentence; the window boundary at
exactly the configured hours; email search super-admin only and no email ever
printed; filters, sort, paging and malformed URLs; the same query count for 5 and
60 rows; the review's numbers; the delete's cascade, the sold-vibe/receipt
survival and the remix un-linking; one audit row per account and never one for
an account that was not deleted; chunking; a failing chunk retried one by one;
one poisoned account costing only itself; a sale landing mid-delete (the real
`ProtectedError`); a review going stale (appeal arrives, hold lifted); the
phrase, reason, limit and permission enforced server-side; CSRF; the hourly rate
limit answering with a message, not a blanket 403; notices (one each, no
evidence, time budget, dead provider, after commit only); the `no-store` /
`Vary` headers; and the stylesheet/template conventions (`minmax(0, 1fr)`,
44 px targets, no inline code).

The security and configuration rows above add `HostileInputTests` (ids, control
characters, invisible reasons, cache headers, hostile usernames, log forging,
unlinkable names), `TunablesTests` (each setting really drives what it names),
`EnvParseTests` and the extra `SettingsTests`. A separate real-browser run
(headless Chromium) covered tampered `sessionStorage`, control characters in the
live search, an HTML/JS-laden username and evidence through list → review →
delete, a token-less POST and a moderator probing the URLs.
