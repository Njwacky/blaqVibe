# BlaqVibes — Admin User Search & Role Change Spec

**Answer to:** “As a superuser I want to search for someone, maybe to change his
role — what is the best way to do that in a big app like this one?”

**Short version:** search first, act on one person at a time, and make the write
path the only path.

* `/admin/roles/` is a **ranked, bounded search**, not a user list.
* `/admin/roles/<username>/` is the **one page that changes a role**.
* `users/roles.apply_role_change()` is the **only writer** of `Profile.role`:
  guards, audit row, notification and email all live there, so a second caller
  (an API, a bulk importer, a management command) cannot ship a weaker path.

---

## 5 Whys — why not “list every user + a role dropdown per row”

The shipped page was `User.objects.all().order_by('username')` with one
`<form>` per row.

1. **Why is that wrong at scale?** The page cost grows with the company. At 40k
   users it is 40k rows of controls: slow to render, slow to transfer, unusable
   on the phone of the moderator who is reacting to a report.
2. **Why is it dangerous?** Every row is an armed weapon. One stray click in a
   40k-row list silently changes a stranger’s access, and the operator has to
   scroll-hunt for the row they changed to check it.
3. **Why isn’t “just add a filter box” enough?** The operator’s input has no
   fixed shape: `@kwame`, `kwame`, `kwame@mail.com`, `Kwame`, `kwam`. A server
   filter (`username__icontains`) has to be told which of those it is. A ranked
   search decides for them, and the exact identifier takes them straight to the
   person.
4. **Why does a search need guards?** Because the thing behind it is a
   privilege change. Search solves “which account”; guards solve “should this
   write happen at all”: no self-promotion/demotion, no demoting the last
   superadmin, a reason for the audit trail, and a typed confirmation in the
   dangerous direction (granting).
5. **Why the notification?** A silent privilege change is how an insider keeps
   access nobody knows they have. The affected person is the one human
   guaranteed to notice a change that should not have happened — so they get an
   in-app notification and an email, with the reason the operator gave.

---

## The flow

```text
/admin/roles/            →  ?q=@kwame        ranked, bounded results (20)
                            ?q=kwame         (2+ matches → list, 1 exact → jump)
                            ?role=admin      everyone holding one role
                            (nothing)        role totals + who has power + log

/admin/roles/kwame/      →  GET   the person: identifiers, account state,
                                  current role, role history for THAT account
                            POST  change it: role + reason (+ confirm when
                                  granting), audited, notified, redirected
```

### Ranking (one SQL pass, `users/user_search.py`)

| Match | Score | Why that weight |
|---|---|---|
| `username` exact | 100 | “I know who this is” — the strongest signal |
| `email` exact | 90 | Same identity, different spelling |
| allauth alternative email exact | 88 | Second confirmed address; `User.email` holds only one |
| `username` prefix | +60 | Prefix beats interior on a handle |
| `email` / alt-email prefix | +55 / +52 | Same, one step weaker |
| `username` contains | +30 | Weakest identity match, still worth showing |
| alt-email / email contains | +22 / +20 | Last resort |

Weights are cumulative, so a prefix match (90) always sorts above an interior
match (30) without a second pass. Scores are `Case/When` annotations, so
`LIMIT 20` plus `ORDER BY` stay in the database: the page never materialises
more than 20 rows, and `total` is a `COUNT` on the same query for the honest
“showing 20 of 340 matches — refine” line.

* `@` is stripped from the query, whitespace collapsed, length capped at 80.
* Queries under 2 characters return nothing on purpose — a one-letter search is
  a sequential scan that matches half the network.
* Every term must match (`kwame ghana` narrows, it does not widen).
* **Postgres** additionally uses `pg_trgm` (`TrigramSimilarity`, threshold 0.45)
  so `kwamme` still finds `kwame`. If the extension is missing, the query raises
  once, the module remembers (`_TRIGRAM`), and every later search uses the
  portable path — a missing optional index must never 500 an admin page.
* **SQLite** (dev/CI) has no typo tolerance by design: `difflib` over usernames
  means loading every username, i.e. exactly the unbounded work this page
  removed.

### Guards (`users/roles.py`)

| Attempt | Result |
|---|---|
| Role not in `user → moderator → admin → superadmin` | Refused, `set_role_denied` audited |
| Actor is not `profile.role == 'superadmin'` | Refused (checked in the engine, not only the view) |
| Target is the actor | Refused, `set_role_denied` audited |
| Target is the last superadmin (checked again inside the transaction, row locked) | Refused — nobody locks everyone out |
| Reason shorter than 5 characters | Refused |
| Granting a higher role without typing the target's username | Refused |
| New role equals the current role | No-op (`info`), no audit row, no email |
| Demotion | Allowed with a reason only — friction on the fail-safe direction makes people delay a demotion they should just do |

**Two axes, deliberately separate.** `Profile.role` governs BlaqVibes' pages
(`/admin/dashboard/`, `/admin/roles/`, moderation). Django's `is_staff` /
`is_superuser` govern `/blaq-admin-secure/`. The engine keeps the app axis
coherent (`admin`/`superadmin` ⇒ `is_staff=True`; `user`/`moderator` ⇒ False)
but **never silently clears `is_superuser`**: a demotion that looked like it
revoked the Django admin account, but did not, is worse than the flag itself.
When the target keeps it, the success message and the audit row say so.

### The write path

1. Guards (above), all before any write.
2. `transaction.atomic()` + `select_for_update()` on the profile row, guards
   that depend on a count re-checked inside the lock, then the write, then the
   `AdminLog` row — all in one transaction.
3. `transaction.on_commit(...)` sends the in-app notification
   (`gallery.notify.notify(kind='role')`) and the email
   (`users.emails.send_generic_email`) — never inside the transaction, so a
   rolled-back change cannot have mailed anybody, and a dead mail server cannot
   undo a completed change.
4. `AdminLog.target` = `@kwame: user→moderator — <reason>`, which the audit page
   already renders and the person's own page filters on
   (`target__startswith='@kwame:'`), giving per-account role history for free.

---

## SQL notes for a big user table

`users/migrations/0025_pg_trgm_user_search_index.py` — Postgres only, best
effort, `atomic = False` so `CREATE INDEX CONCURRENTLY` can run:

```text
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX CONCURRENTLY IF NOT EXISTS users_user_username_trgm  ON auth_user            USING gin (username gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS users_user_email_trgm     ON auth_user            USING gin (email    gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS users_emailaddress_email_trgm ON account_emailaddress USING gin (email gin_trgm_ops);
```

Every statement is idempotent and failures are logged, not raised: the app role
may not have `CREATE EXTENSION` rights on a managed Postgres, and search already
degrades without trigram. An operator who prefers not to grant those rights can
paste the same statements into the Supabase SQL editor once.

---

## Why not the alternatives

* **Django admin autocomplete (`/blaq-admin-secure/`)** — the wrong audience.
  `profile.role` is the app's access axis; Django admin is the escape hatch for
  operators with `is_staff`, and it is exactly the surface the RBAC work moved
  away from.
* **`user.profile.role` editable in a list view (editable grid)** — a grid of
  armed dropdowns is the old page with new paint.
* **Bulk import of role changes from CSV** — a role change is 1–5 events a
  quarter, each with a human reason. Bulk tooling would be used once, misuse
  would be invisible in the audit trail, and the guard rails (self, last
  superadmin, confirmation) have no place to live.
* **Autocomplete/typeahead on every keystroke** — full-page GET search is fine
  for a rare admin action and works with JS disabled; a live endpoint adds a
  second thing to secure, rate limit and keep in sync with the guards.
* **Searching emails for non-superadmins** — emails are personal data. The
  search module is only ever called from `@superadmin_required` views; keep it
  that way.

---

## Files

| File | What |
|---|---|
| `users/roles.py` | `apply_role_change()` — guards, transactional write, audit, notify, email; `ROLE_ORDER`, `ROLE_GUIDE` |
| `users/user_search.py` | `search_users()`, `users_with_role()`, `elevated_rows()`, `role_totals()` — ranked + bounded, trigram with fallback |
| `users/admin_views.py` | `manage_roles` (search / role filter / landing) and `manage_user_role` (GET + POST on one person) |
| `users/urls.py` | `/admin/roles/`, `/admin/roles/<username>/`, plus the legacy `set_role` alias `/admin/roles/<username>/set/` |
| `templates/users/manage_roles.html` | the search page (3 states) |
| `templates/users/manage_role_detail.html` | the person page and the form |
| `static/gallery/css/admin-roles.css` | styles, 5 Whys header, stacks below 760px |
| `users/decorators.py` | `Http404` now propagates — a missing account is a 404, not “It’s not you, it’s me” |
| `gallery/models.py` + `0043` | notification kind `role` |
| `users/migrations/0025` | the optional trigram indexes above |
| `users/test_role_admin.py` | 43 tests: ranking, bounding, every guard, audit content, notification + email, view permissions, the alias URL |

## Test gates

```bash
python manage.py test users.test_role_admin        # the new suite
python manage.py test gallery users                # scripts/ci.sh runs this
```
