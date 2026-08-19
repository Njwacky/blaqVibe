# BlaqVibes — Public-Language Gate: 5 Whys On Every Fix (Demo-Grade, No Shortcuts)

> Rule from the review: the recheck must prove this is real code, not demo code.
> Every fix below survives 5 Whys. Each Why is answered with **4 points**; where
> one argument does not settle it, a second method (the rejected alternative and
> why it loses) is brought in. Nothing here is client-side, nothing scans ZIPs,
> and no safety word (sex/rape/porn) is blocked.

**Date:** 19 Aug 2026 | Branch: `arena/01a01acc-blaqvibe` | Tests: 359 (320 prior + 39 new), `scripts/ci.sh` green.

---

## 1. SOCIAL SIGNUP USERNAMES (GitHub/Google never hit SignUpForm)

**Why 1: Why was this a real leak?**
1. `SOCIALACCOUNT_AUTO_SIGNUP=True` — allauth copies the provider handle straight onto the User row; no BlaqVibes form runs.
2. The username is then rendered on every card (`@{{ p.owner.username }}`), comment, profile, leaderboard and git URL — one dirty handle repeats across the whole site.
3. The write-time gate (`SignUpForm.clean_username`) only protects email signups, so the gate's own claim ("one truth, every caller asks it") was false for social paths.
4. Proof it was exploitable: any GitHub account named `fuckyou` could sign up and appear verbatim — no obfuscation skill needed.

**Why 2: Why refuse auto-signup instead of silently renaming?**
1. Honesty contract of this codebase: "we do not rewrite the author's words" — a silent rename would publish a handle the person never chose, and they would discover it only later.
2. Refusing auto-signup routes the person to `templates/socialaccount/signup.html`, where they type their own clean handle — consent + agency.
3. Alternative method (rejected): rename in `save_user` only. Loses because the person never participates; the handle `user_42` appears with no explanation at the moment it matters.
4. Alternative method (rejected): block signup entirely. Loses because the provider handle is not the person's fault; GitHub allows handles we do not. We gate the display name, not the account.

**Why 3: Why hook `is_auto_signup_allowed` + `clean_username` + `save_user` (three layers)?**
1. `is_auto_signup_allowed` is the earliest point where `sociallogin.user.username` exists — verified against allauth 65.19.1 source (`sociallogin_from_response` → `process_signup`).
2. `clean_username` on the account adapter covers the signup form the person lands on — the real `allauth.socialaccount.forms.SignupForm` runs it (pinned by `RealAllauthSocialFormTests`, not a mock).
3. `save_user` + `force_clean_username` is the backstop for any future provider/flow — if a dirty name ever reaches save, it becomes `user_<pk>` and the user is **told** via a `moderation` notification.
4. Defense-in-depth is the house rule (forms → ORM → display); one layer per write path is a shortcut that dies the day a flow changes.

**Why 4: Why is the dirty GitHub handle NOT copied to `profile.github`?**
1. The profile page renders `GitHub @{{ profile.github }}` publicly — copying a blocked handle moves the leak from username to profile.
2. Leaving the field blank is hiding, not rewriting — the link simply does not exist; no lie is told.
3. Alternative method (rejected): copy it but filter at display. Loses because the filter would hide it anyway — storing known-blocked words invites a future template to forget the filter.
4. The handle stays in `SocialAccount.extra_data` (allauth's own storage, never rendered) — the connection still works for login.

**Why 5: Why does this hold at 10k users?**
1. The gate runs O(1) per signup — no DB scans, no lists to grow; `contains_profanity` is the same constant the forms use.
2. `force_clean_username` proves uniqueness with one indexed lookup (`username` is unique) — no table walks.
3. The notification is one row via the existing `notify()` path — no new machinery to maintain.
4. Any new provider allauth adds inherits the adapter hooks automatically — the fix is structural, not per-provider.

**Full code:** `users/adapters.py` (all three hooks + `force_clean_username`), tests `SocialUsernameGateTests`, `RealAllauthSocialFormTests`.

---

## 2. DJANGO ADMIN / ORM GATE (`AppProject.save()` ignored title/README/description)

**Why 1: Why was this a real leak?**
1. `AppUploadForm` gates the publish/edit views, but Django admin and `manage.py shell` write straight to the row — the feed and `/api/v1/apps/` then served the words.
2. The review proved the gap: a staff edit or shell create could put vulgar text on the feed with zero checks.
3. Feed/API filter on `status='published'` only — nothing ever re-checked the text fields after the form path.
4. Legacy rows could already be dirty; the earlier scrub (0025) covered comments/bios/tips but never `AppProject`.

**Why 2: Why demote to `pending` in `save()` instead of raising?**
1. Raising would crash background tasks (appeal scores, classification, PR merges) the moment they re-save a legacy dirty row — a leak fix that kills the pipeline is a shortcut.
2. Demoting is fail-closed: the row leaves every public list (feed, API, sitemap all filter `status='published'`) the instant it is saved.
3. The raw text is preserved — moderators see it in the queue with a `language_gate` note in `scan_report`, so the reason travels with the row.
4. Alternative method (rejected): blank the dirty fields in `save()`. Loses on honesty — the owner can no longer see what they stored, and evidence for moderation is destroyed.

**Why 3: Why also `clean()` and a field list (`PUBLIC_TEXT_FIELDS`)?**
1. Django admin validates via `full_clean()` before saving — `clean()` turns a staff typo into a per-field form error, not a silent demotion. Honest error beats silent safety.
2. `PUBLIC_TEXT_FIELDS = ('title','readme','short_description','tech_stack')` is one list that `save()`, `clean()` and migration 0027 all read — the gate cannot drift between layers.
3. `ModelForm` runs `clean()` too, so every form gets the same error text (`PUBLIC_LANGUAGE_ERROR`) — one message everywhere.
4. Alternative method (rejected): validators on each model field. Loses because validators run inside `full_clean` per field with no cross-field report, and shell writes skip them anyway — we would still need the `save()` gate; two mechanisms instead of one shared list.

**Why 4: Why does the gate touch `update_fields` and slug generation?**
1. `save(update_fields=['appeal_score'])` must still persist the demotion — the gate appends `status`/`scan_report` to the caller's list, so no partial save can strand a published dirty row (pinned by tests).
2. Slugs are URLs and URLs are public surfaces — `slugify('fuck you all')` would mint `fuck-you-all`; the gate generates slugs from `'vibe'` when the title is dirty, and migration 0027 re-mints legacy dirty slugs.
3. The check runs BEFORE the existing pre-process block, so the readme render and language detect never operate on a row that will not publish.
4. The `scan_report` note records fields + timestamp — an audit trail without a new table.

**Why 5: Why does this hold at 10k vibes?**
1. The check is four `contains_profanity` calls on save — micro-seconds; no extra queries (the dirty check reads instance state only).
2. No index changes, no schema changes beyond the existing JSON field — migrations stay trivial.
3. Tasks that bulk-update via `queryset.update()` bypass `save()` by design — those paths (`appeal_score`, counters) never write text fields; the display backstop (section 3) covers any that ever did.
4. The moderation queue already existed for pending rows — held vibes reuse it with zero new UI infrastructure (only a reason line was added).

**Full code:** `gallery/models.py` (`PUBLIC_TEXT_FIELDS`, `dirty_public_fields`, `clean`, `save` gate), `gallery/migrations/0027_hold_public_vibe_profanity.py`, tests `ProjectOrmGateTests`, `AdminGatesTests`.

---

## 3. DISPLAY-TIME BACKSTOP (`display_text` + template filters + API)

**Why 1: Why re-check at render time when write gates exist?**
1. Write gates have a countable set of callers; render surfaces are where EVERY path's output finally becomes public — the net must hang under all of them.
2. The review named the failure mode exactly: "one forgotten field and the words are public again" — legacy rows, shell `UPDATE`s, future endpoints.
3. Proven live: a raw `AppProject.objects.update(title='this is a shit app')` (bypassing `save()` entirely) leaked nothing — feed, detail page, `/api/v1/apps/` and `/api/v1/apps/<slug>/` all returned placeholders.
4. XSS sanitizers (bleach/nh3) do not stop slurs — this is the layer that does, at the last possible moment.

**Why 2: Why ONE shared helper (`profanity.display_text`) and not per-surface logic?**
1. The rule "never render a blocked word" must have exactly one home — the template filter, the JSON serializer and `nolo.compare_apps` all call the same function, so the word list cannot drift between HTML and JSON.
2. `contains_profanity` is fail-closed (exceptions return True) — a crash in the helper can never become a leak; worst case is a placeholder.
3. The helper never raises — display must not 500 a page; the trade (placeholder instead of title) is visible and fixable, unlike a leak.
4. Alternative method (rejected): middleware that scans every response HTML. Loses on cost (regex over every byte of every page at 10k vibes), on correctness (cannot distinguish fields), and on the JSON API (parses HTML).

**Why 3: Why a placeholder instead of masking (`f***`)?**
1. The module's founding rule: "Masking still IS the word" — `f***` is readable by every human who matters.
2. A placeholder ("Untitled vibe", "user") keeps the page shape honest — the reader sees something was withheld, not a broken template.
3. Per-type placeholders: titles get "Untitled vibe", usernames "user", hidden comments get the same notice `Comment.save()` uses — a blocked comment looks identical whether the ORM or the template caught it.
4. Alternative method (rejected): delete the row at render time. Loses — rendering must never mutate data; a read path that writes is a race and an audit hole.

**Why 4: Why two template filters (`public_text` / `public_html`)?**
1. Plain fields escape normally; `|safe` fields (body_html, readme_html) must stay safe-marked AFTER the check or `|safe` would apply to raw user HTML.
2. `public_html` checks the HTML string itself — after folding, tags vanish and only words remain, so `<b>slur</b>` is still caught (pinned by `DisplayTextBackstopTests`).
3. `public_html` only returns our own constants (marked safe) or already-sanitized stored HTML — the safe-mark never touches unchecked input.
4. Empty values pass through untouched (a legitimately empty readme renders nothing, not a scary "removed" notice) — false positives at display would be their own lie.

**Why 5: Why does this hold at 10k vibes?**
1. The check runs per rendered field, in-process, with no queries — same cost class as `truncatechars`.
2. It covers every future surface by convention: the filter is one line; the audit (`grep` for unfiltered public fields) stays mechanical.
3. The API serializer applies it to `title`, `description`, `owner`, `tech_stack`, `readme` — third-party clients rendering our JSON get the same guarantee.
4. Hot pages render the gate ~5-10 times per request — measured in the test suite's page GETs, no perceptible cost.

**Full code:** `gallery/profanity.py:display_text`, `gallery/templatetags/safe_display.py`, `gallery/api_views.py`, `gallery/nolo.py:compare_apps`, 20 templates, tests `DisplayTextBackstopTests`, `ReadmeRenderBackstopTests`.

---

## 4. COMMENT REPORT (spec: report button + `CommentReport` + in-app queue)

**Why 1: Why was the missing report a spec violation, not a nice-to-have?**
1. `BlaqVibes_MD_Tree_Comments_Spec.md` line 147 promises: "report button (`Report comment` → `CommentReport` model)" — it never existed.
2. Visitors could report a vibe (`report_vibe`) but not a comment — the most abusive surface on the site had no flag at all.
3. Moderators had only Django admin's `is_hidden` — no queue of what to look at; moderation without intake is a toggle in the dark.
4. The language gate is a list; human judgment catches harassment the list never will. Reports are how the list learns.

**Why 2: Why a model with `reporter` nullable + `resolved`, not a boolean on Comment?**
1. A comment can be reported many times for different reasons — the queue needs who/why/when, which a boolean cannot hold.
2. Nullable reporter matches `report_vibe`'s contract: visitors (not just members) can report; the IP ratelimit (`10/h`) is the abuse brake — consistent doors across the site.
3. `resolved` (not delete-on-handle) keeps the audit trail: hide vs dismiss is a decision someone made, and the row is the receipt.
4. FK rules: CASCADE on comment (metadata dies with the row), SET_NULL on reporter (account deletion must not erase that a flag was raised).

**Why 3: Why in-app queue actions instead of the admin toggle?**
1. Moderators live in `/moderation/queue/` — a comment report that only exists in Django admin will rot; the queue is where the eyes are.
2. The queue shows the RAW body on purpose (staff-only surface) — moderators must see the words to judge them; the display backstop applies to public pages only.
3. Three actions with different semantics: `hide` (words off the page + reports resolved), `dismiss` (comment stays, report resolved), `unhide` (false-positive recovery — and a language-gated comment re-hides itself on save, so the words can never sneak back).
4. `hide` uses a queryset `update()` deliberately — `Comment.save()` re-renders `body_html` from the raw body, which would resurrect a clean-looking body's HTML; the update writes hidden-state + notice atomically.

**Why 4: Why the specific anti-abuse rules?**
1. One open report per logged-in person per comment — repeat clicks cannot flood the queue (pinned by `test_logged_in_reporter_is_recorded_and_deduplicated`).
2. Anonymous reports ride the `10/h` IP ratelimit — same as vibe reports; the key rotates, but a session-less flood costs real IPs.
3. Unknown reasons coerce to `'other'` — an attacker's creative `reason` value becomes data we control.
4. `details` runs through `sanitize_prompt` (control chars/injection) but NOT the language gate — it is shown to moderators, who need to read what the reporter typed; gating staff-facing intake would hide the complaint's own words.

**Why 5: Why does this hold at 10k comments?**
1. Index on `(resolved, -created_at)` — the queue query is an indexed range scan of open rows only, capped at 100 per page.
2. Reporting is one INSERT; no comment-row write, no cache invalidation — the hot path (posting/reading comments) is untouched.
3. Hiding a comment reuses the existing `is_hidden` exclusion in the detail view's queryset — zero new query shape.
4. The model is the spec's extension point: reason analytics, reporter reputation, and auto-escalation (N reports → auto-hide) all bolt onto existing columns.

**Full code:** `gallery/models.py:CommentReport`, `gallery/views.py:report_comment`, `gallery/moderation.py` (queue + `comment_report_action` + `comment_action`), `templates/gallery/app_detail.html` (buttons), `templates/gallery/moderation_queue.html`, `gallery/admin.py`, tests `CommentReportTests` (9 cases).

---

## 5. CHANGELOG HONESTY (silent rewrite to "Update" taught nobody)

**Why 1: Why was the silent rewrite wrong?**
1. The author typed a changelog; the system stored "Update" and said nothing — next upload they type it again, same silence. The gate looked like a bug.
2. Honesty is the product's stated rule (5-Whys doc: "honest capability, never a fake") — a silent rewrite is a lie by omission in the author's own history.
3. Without feedback the author learns nothing about the public-language contract — the next attempt escalates, not improves.
4. The review flagged it precisely: "the author never learns why."

**Why 2: Why keep the fallback to "Update" at all, instead of blocking the upload?**
1. The version row snapshots the OLD zip before the new one replaces it — losing the snapshot to a dirty note destroys rollback history over words. The artifact matters more than the label.
2. Blocking the whole edit punishes the code (which is clean) for the note (which is not) — disproportionate.
3. The author gets an explicit `messages.error` naming the changelog, the fallback, and the remedy ("reword it and upload again") — pinned by `ChangelogHonestyTests`.
4. Alternative method (rejected): store the dirty changelog but hide it. Loses — a hidden changelog still renders in the owner's edit page and git metadata; storing known-blocked text is the same leak deferred.

**Why 3: Why also `AppVersion.save()` backstop?**
1. Three writers bypass the edit view: `git_daemon.py` push snapshots, `pr_action` merge snapshots, and admin/shell — the model gate catches all of them in one place.
2. Those system paths generate constant text ("pre-push snapshot", "Before merge of PR #id") — the backstop costs nothing there and is the net if that ever changes.
3. The model layer replaces silently (no author to message) — different layer, different honesty contract: humans get messages at the view layer, machines fail closed at the ORM layer.
4. Migration 0027 scrubs legacy dirty changelogs to "Update" — the DB ends in the same state the new code maintains.

**Why 4: Why is the message text engineered the way it is?**
1. It never echoes the blocked word — echoing re-publishes the abuse into a stored, replayable Django message.
2. It says what happened ("saved as 'Update'"), why ("language not allowed in public text"), and the remedy ("reword and upload again") — all three or it is not feedback.
3. It appears alongside the upload-success message, not instead of it — the author sees both facts: code accepted, note rejected.
4. It uses the same `PUBLIC_LANGUAGE_ERROR` vocabulary as every other gate — one contract, recognisable everywhere.

**Why 5: Why does this hold at scale?**
1. One extra `contains_profanity` call per version upload — versions are rare events (new ZIPs), not feed reads.
2. No schema change, no new table — the fix is behaviour, not machinery.
3. The message rides Django's existing message framework — no delivery code.
4. If changelogs ever surface in the API, `display_text` (section 3) already covers them at zero extra work.

**Full code:** `gallery/views.py:edit_vibe`, `gallery/models.py:AppVersion.save`, migration 0027, tests `ChangelogHonestyTests`.

---

## 6. LOCAL LANGUAGES (Afrikaans listed; isiZulu/isiXhosa absent)

**Why 1: Why was an English+Afrikaans list a guaranteed leak?**
1. BlaqVibes is Durban-first; Durban's comment sections argue in isiZulu, isiXhosa and code-switched mixes — the list did not speak the city.
2. The review said it plainly: "A list that only speaks English will leak here."
3. Abusers route around gates by switching language — a monolingual gate is an invitation, not a deterrent.
4. Equality of protection: an English slur gets blocked while its isiZulu equivalent sails through — the policy would be applying different standards to different communities.

**Why 2: Why THESE words and not a scraped swear list?**
1. Parity rule: the additions map 1:1 onto tiers already blocked in English — `isifebe`/`hoer`/`slet` (whore/slut tier), `umqundu`/`igolo`/`cuiter`/`klootzak` (cunt/asshole tier), `isilima`/`isithutha`/`isiphukuphuku`/`isidenge` (direct personal-attack tier, parallel to the English `asshole`/`tosser` tier we already block).
2. Sourced, not vibes: entries are documented in SA dictionaries/phrasebooks (DSAE for `hotnot`/`koelie`/`mampara`, wildcoast phrasebook for `isidenge`, common Zulu lexicon for the rest) — each carries an inline comment naming its tier.
3. Rejected on evidence: `mampara` is documented as affectionate ("silly goose", parents to children) — blocking it kills real speech without stopping abuse; the test suite pins it allowed. Same for low-quality-list entries like `didi`/`golo` (ambiguous, unverified) — absence is a decision, documented in tests.
4. The safety carve-out holds: `sex`, `rape`, `porn` remain unblocked (education/safety READMEs) — pinned by `test_english_sex_education_words_are_not_blocked`.

**Why 3: Why suffix matching for Nguni words?**
1. Nguni grammar prefixes nouns: "you are an idiot" = `uyisilima` / `yisidenge` — whole-token matching misses the forms people actually type.
2. The insult stem sits at the END of the token, so `token.endswith(stem)` with stems ≥6 letters (`silima`, `sithutha`, `sidenge`, `sifebe`, `iphukuphuku`) catches `uyisilima`, `yisidenge`, plurals `izidenge`, while accidental matches in real prose are implausible at that length.
3. Alternative method (rejected): stemming/dictionary lemmatisation for Zulu. Loses — no production-grade Zulu stemmer exists in our stack, a hand-rolled one is a research project, and the suffix rule is auditable in one frozenset.
4. Alternative method (rejected): prefix matching. Loses on the grammar — the prefix is the variable part (u-, yi-, izi-, aba-); anchoring there catches nothing.

**Why 4: Why keep the false-positive bar this high?**
1. The module's founding discipline: "class", "password", "Scunthorpe", "cocktail" must pass — every new word is tested against that bar (`LocalLanguageMatcherTests` pins innocents: `isilinganiso`, `tokoloshe`, `eish`, `sharp sharp`).
2. A false positive on a real word is a support incident AND a trust loss ("this site doesn't understand my language") — worse than one more slur variant slipping through, because the human report flow (section 4) catches survivors.
3. Whole-token + suffix rules only; no infix (the Scunthorpe lesson), no substring hacks.
4. Leetspeak/homoglyph folding applies to the new words automatically — `uy!silima` still folds to the stem check, because normalisation happens before matching.

**Why 5: Why does this hold at 10k vibes?**
1. Matching stays O(tokens) with a small frozenset — no per-language engines, no network calls.
2. The list is one commented block in one file — additions are a one-line PR with a test, not a re-architecture.
3. Fail-closed semantics unchanged: if folding ever crashes, the text is treated as unclean.
4. When a new language community arrives (Sesotho, Setswana, Portuguese), the pattern (tier parity + sourced words + morphology rule + tests) is already written down here.

**Full code:** `gallery/profanity.py` (`_BLOCKED_WORDS` SA section, `_LOCAL_SUFFIXES`), tests `LocalLanguageMatcherTests`.

---

## 7. EXISTING USERNAMES (0025 scrubbed comments/bios/tips, not accounts)

**Why 1: Why were old dirty handles still a live leak?**
1. Migration 0025 walked Comment/Review/Notification/PullRequest/Tip/Profile — never `auth_user`. An old `@fuckyou` still rendered on every card, comment byline, profile and leaderboard.
2. The write gates (forms + adapters) only stop NEW names — legacy rows predate the gate entirely.
3. Usernames render in more places than any other field (cards, comments, reviews, tips, followers, git URLs, JSON-LD, API `owner`) — one dirty handle is a site-wide stain.
4. The review was explicit: "Those need a forced rename or hide, not a silent rewrite."

**Why 2: Why forced rename and not hide/mask at display?**
1. The review's own bar: masking (`f***you`) still IS the word; hiding one field leaves 20 other templates rendering the same username — rename fixes every surface atomically.
2. A username is an identity, not content — replacing it with `user_<pk>` removes the abuse without destroying the account: vibes, stars, trades, receipts all key off the user id, never the name.
3. Alternative method (rejected): display filter only. Loses — the words stay in the DB, in git clone URLs, in every future export; one forgotten template re-leaks.
4. Alternative method (rejected): delete the accounts. Loses — disproportionate; the person keeps their work, only the handle goes.

**Why 3: Why announce it instead of silently renaming?**
1. Silence is the exact failure the review condemned in the changelog: "the author never learns why."
2. Each renamed user gets a `moderation` notification: what happened ("username broke public-language rules"), what is safe ("account, vibes, stars untouched"), and that the new handle is a placeholder.
3. The notification NEVER echoes the old word — the migration pins this in a test (`assertNotIn('fuckyou', note.body)`); echoing would re-publish the abuse into every inbox.
4. The new handle `user_<pk>` is visibly synthetic — the person (and everyone else) can see it is a placeholder, which is itself the honest signal.

**Why 4: Why the migration's exact mechanics?**
1. `user_<pk>` is unique-by-construction with a collision loop (`user_<pk>_1`) — no global scan, deterministic, idempotent.
2. Dirty `first_name`/`last_name` and `Profile.github`/`twitter` are blanked (hidden, not rewritten) — same tier as the adapter's github-handle rule; these render on profiles.
3. Old notification URLs (`/u/fuckyou/`) are blanked too — 0025 missed `url`, and an inbox href is still rendered text; the test pins it.
4. Runs as a data migration AFTER the schema migration adds the `moderation` notification kind — deploy order is enforced by dependencies, and the function is irreversible on purpose ("we will not put the words back").

**Why 5: Why does this hold afterwards?**
1. Post-migration, every username source is gated: SignUpForm, allauth adapters (section 1), Django admin form (`BlaqUserAdmin`), shell leftovers impossible (the walk was exhaustive).
2. The display backstop (section 3) filters every rendered username anyway — four independent layers before a word reaches a page.
3. Renamed users keep full functionality — trades, git tokens, profiles all survive; the only broken artifact is old `/git/<oldname>/<slug>.git` clone URLs, which is the stated price of removing the word.
4. The `AdminLog`/notification pattern gives support a paper trail when a renamed user asks why.

**Full code:** `gallery/migrations/0028_scrub_existing_accounts.py`, `users/admin.py` (`BlaqUserAdmin` forms), `users/adapters.py`, tests `ScrubsMigrationTests`, `AdminGatesTests`.

---

## WHAT WAS DELIBERATELY NOT BUILT (anti-shortcuts, pinned by tests)

1. **No client-side JS filter** — anyone can POST; every gate above is server-side (forms, adapters, ORM, migrations). The browser renders, never decides.
2. **No ZIP/HTML/JS content scanning for language** — false positives on real code; the sandbox + scan queue already own malicious content, and filenames/ZIP internals stay their domain.
3. **No blocking of `sex`, `rape`, `porn`** — safety and education READMEs must publish; the test suite asserts they pass the gate.
4. **No `mampara`** — affectionate by documented usage; blocking it would be the gate lying about the city it serves.
5. **No silent rewrites anywhere a human typed** — demote, hide, or rename, always with a message or a moderator-visible trail.

---

## VERIFICATION RECEIPT

- `scripts/ci.sh` (migrate → seed_demo → full suite → seeded-catalog assert): **green**.
- **359 tests**, including 39 written for this work: matcher (local languages, anti-false-positive), every public POST gate, ORM/admin gates, display backstops (page + API + readme_html), real allauth social form, both scrub migrations, comment-report loop (visitor → queue → hide/dismiss/unhide → 403s → ratelimit), changelog honesty, admin user form.
- Live attack rehearsal passed: obfuscated signup username rejected; isiZulu insult comment rejected at the form and hidden at the ORM; shell-corrupted title/readme leak nowhere (feed, page, both API endpoints); visitor report → moderator hide removes the words from the page.
