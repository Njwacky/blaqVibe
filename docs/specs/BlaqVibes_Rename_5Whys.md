# BlaqVibes — Identity & Names 5 Whys (PUBG Rename Rule + Name Style)

**Date:** 21 Aug 2026 | **App status before:** no username-change path at all; no
name styling; feed cards had no creator link | **Requested:** PUBG-style rename
rule (paid account OR stars, not free-for-all) + customizable name (font,
anime effect, size) to show off, then audit everything missing around it.

**Rule implemented (the PUBG card, translated to BlaqVibes):**

| PUBG | BlaqVibes |
|---|---|
| Rename card costs UC / ships with Royale Pass | **100 ★ burned**, OR free while **Pro** |
| One rename at a time, cooldown after use | **30-day cooldown** for everyone, even Pro |
| Name is your identity in the lobby | `/u/<name>/` profile, `/git/<name>/<vibe>.git` clone URLs, notifications |
| — | Old name **reserved 90 days**, old links **302-redirect** |
| — | Name style (20 people-styles + font / color / size / anime fx): **20 ★ per change**, free while Pro |

`users/rename.py` is the only writer of `User.username` after signup. 36 new
tests in `users/test_rename.py`. Ledger reasons added: `rename_spend`,
`style_spend`.

---

## The 5 Whys chains

### 1. Why can't you rename for free any time you want?

1. **Why gate renames at all?** The username is identity: profile URLs, git
   clone URLs (`/git/<name>/<vibe>.git`), trade receipts, notification links.
   Free renames make identity disposable.
2. **Why is disposable identity bad?** Disposable identity is how ban evasion
   and follow-phishing start — a scammer cycles handles faster than victims
   can remember who they blocked.
3. **Why stars OR Pro specifically?** Pro is the paid path (a rename card
   ships with the pass). Stars are the earned path — they come from other
   humans trading your work, so a farm of throwaways can never afford one:
   the 5 ★ welcome grant is 1/20th of the cheapest cosmetic.
4. **Why a 30-day cooldown even for Pro?** The price throttles volume; the
   cooldown throttles frequency. Without it a paid account cycles names
   hourly to dodge moderation searches and follower blocklists.
5. **Why burn the stars instead of paying them to anyone?** Currency moved to
   a fake "house" account is still in the economy. Burned is a **sink** —
   before this feature the ledger had mints (welcome, trades in) and only
   payout holds as sinks. Every sink makes every remaining star worth more.

### 2. Why reserve the OLD username for 90 days?

1. **Why reserve at all?** Renaming frees a handle. The minute a known
   creator renames, a stranger can grab the freed handle and impersonate
   them ("it's me, I renamed — send stars").
2. **Why 90 days and not forever?** Forever means every handle ever used is
   dead weight; the market for names turns into squatting. 90 days covers
   the window in which other people's memory of the old handle is fresh.
3. **Why can the OWNER reclaim early?** Taking your own old name back
   impersonates nobody; the 30-day cooldown already stops ping-ponging.
4. **Why does account deletion clear the reservation (CASCADE)?** The
   deletion screen promises a clean exit; there is no person left to
   impersonate. Money records (Sale/Trade) survive deletion — vanity does not.
5. **Why iexact matching on the check?** `Nolo` freed must not be grabbable
   as `nolo`. Django's own signup form rejects case-only duplicates; the
   rename path must not become the side door around that rule.

### 3. Why do old `/u/<oldname>/` links redirect instead of 404?

1. **Why not let them 404?** Every notification, comment mention, and shared
   link embeds `/u/<name>/`. A rename that vaporises months of inbound links
   is a self-inflicted broken site.
2. **Why redirect from history instead of a redirect table?** UsernameHistory
   already IS the map (who, from what, to what, when) — one indexed lookup,
   no second table to keep in sync.
3. **Why resolve to the LIVE username, not the stored `new_username`?** The
   row is a timeline (A→B→C); following the FK lands on C for free. Storing
   the answer would duplicate state that drifts.
4. **Why does the redirect survive the 90-day window?** If nobody else took
   the name, the visitor's intent ("who was @oldname?") is still the same
   human. If somebody DID take it, the live user wins and the redirect never
   fires.
5. **Why 302 and not 301?** The target can change again (chained renames) —
   a permanent redirect would pin browsers to a URL that can itself move.

### 4. Why is the name style server-rendered from a whitelist?

1. **Why not accept CSS from the user?** `style` attributes are HTML; a
   crafted value can break out of the attribute or smuggle `url()` /
   `expression()` payloads. bleach does not sanitize CSS.
2. **Why render from OUR dicts then?** The template prints only the OUTPUT of
   `users.models.NAME_*`; an unknown slug falls back to the default via
   `.get()`. Nothing user-typed ever reaches the page as CSS — proven by
   `test_tampered_db_value_renders_plain`.
3. **Why no per-user external fonts?** CSP stays strict, the PWA stays
   offline-friendly, and a Google-Fonts request per user per pageview is a
   privacy leak for zero benefit. The stacks ship with the OS or the site's
   own two font files.
4. **Why em-based size classes, not stored px?** A styled name renders on the
   profile header (18 px context) AND in follower lists (14 px context). A
   stored px is wrong in one of them; em scales, and `xl` is capped so
   nobody shrinks a name to invisible or fills a page.
5. **Why is "rainbow"/"shine" a class, not a color?** They are gradients +
   keyframes. Keyframes live in `blaqvibes.css`, versioned with the theme,
   and respect `prefers-reduced-motion`. Inline styles cannot carry that —
   and allowing them to is exactly what chain 4.1 bans.

### 6. Why twenty named people-styles (Coder, Glamour, Charmer, Strict + 16)?

1. **Why people-types instead of more fonts?** A font slug is a technical
   knob. "Coder" is a character other people recognise on a follower list.
   Twenty recipes reuse `NAME_*` — no new injection surface, no new font
   files, no second wallet. The four requested types plus sixteen more
   fill one scannable grid.
2. **Why a slug + recipe, never a user-typed class?** The template prints
   `namepersona-coder`, never the posted string. Unknown slugs degrade to
   Classic via `compose_name_style` (write AND read). Flourish CSS lives
   next to `namefx-*` so motion preferences stay one file. Tests assert
   every recipe is on-whitelist.
3. **Why does picking a card fill font/color/size/fx?** A no-JS POST would
   otherwise save `persona=coder` on top of default dropdowns and lie.
   Filling the four fields means the existing renderer still works if an
   old CSS cache is missing the persona class. Ledger refs record both.
4. **Why does a fine-tune that leaves the recipe clear the persona?**
   Leaving `namepersona-coder` on a gold-serif mix is a lie, and the extra
   class would fight the mix. Classic + mix is the honest custom path.
   Re-selecting the card restores it — clearing is not a lock-out.
5. **Why is Classic not one of the twenty?** Classic is the free default
   everyone already has. Counting it would pad the grid with a no-op.
   Each of the twenty burns the same 20★ as a hand-mixed style.

### 5. Why charge 20 ★ per style CHANGE and not per style unlocked?

1. **Why charge at all?** The style is the flex other people see on THEIR
   pages (feed cards, follower lists, tips). Free styling = every bio a
   rainbow at once = nobody stands out. Priced styling = the style itself
   signals status — the show-off economy the feature exists for.
2. **Why per change, not an unlock registry?** An unlocks-forever store is a
   second wallet to reconcile; a per-change burn reuses the one ledger that
   already reconciles. Charge on the transition; a no-op re-submit costs
   nothing (idempotent by design).
3. **Why is resetting to default always free?** Un-styling must never be
   paywalled — same reason GitHub lets you delete your own README.
4. **Why 20 ★?** Cheaper than the name itself (restyle freely, rename
   rarely) but welcome-grant-proof: 20 ★ is four verified mailboxes per
   restyle — a throwaway farm breaks even on nothing.
5. **Why is the money check under `select_for_update`?** Balance burn +
   style write + affordability check are one atomic step — the same race
   every other wallet move already guards.

---

## Poor-logic / missing audit — findings table

Checked surface: every write path for usernames, every place a username is
rendered or embedded, and the identity-adjacent economy.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | **No username-change path existed.** Only Django admin. Users typo a handle at signup and are stuck forever (identity regret → churn). | Critical gap | ✅ Fixed — PUBG card (`rename_user`) |
| 2 | **Reserved usernames not blocked at signup.** Anyone could register `support`, `blaqvibes`, `nolo`, `billing` — the three working phishing templates. | Critical | ✅ Fixed — shared `RESERVED_USERNAMES` gates signup AND rename |
| 3 | **Impersonation via freed names** (would have shipped with any naive rename feature). | Critical (latent) | ✅ Fixed — 90-day reservation + owner-reclaim rule |
| 4 | **Link rot**: every `/u/<name>/` link in old notifications/comments 404s after a rename. | High | ✅ Fixed — history-backed 302 redirect |
| 5 | **Feed cards had no creator link and no full title** — the card redesign dropped both; `test_feed_links_creator_names_to_profiles` and `test_seed_fills_the_feed` were failing on master. Profile discovery from the feed was gone. | High | ✅ Fixed — title + `by @creator` (styled) + ★/⬇ restored |
| 6 | **No star sinks** besides payout holds → inflation pressure. | Medium | ✅ Improved — rename (100 ★) and restyle (20 ★) burns |
| 7 | **Pro had no visible perks** to a stranger (who-viewed/AI README are private to the Pro user). | Medium | ✅ Improved — rename card + name styling are public flexes |
| 8 | **Rename oracle**: a free endpoint would let a bot probe which names are taken/reserved. | Medium | ✅ Mitigated — 5/h ratelimit + cooldown on the failure path |
| 9 | **Username uniqueness is case-insensitive only at the form layer** (Django `UserCreationForm`), the DB unique is case-sensitive. The rename path re-implements the `iexact` check so it cannot become the side door. | Low (documented) | ✅ Kept consistent |
| 10 | **Git clone URLs embed the username** (`/git/<name>/<vibe>.git`). After a rename, old clone URLs 404. | Low (trade-off) | 📝 Documented — profile links redirect; git remotes must be re-shared. A git-URL redirect was considered and rejected: silent remotes mask who is pushing. |
| 11 | **Old notifications keep the old `/u/<name>/` URL text.** | Low | ✅ Masked — those links now land on the redirect too. |
| 12 | **allauth social signup** could still mint a reserved-looking name generated from a provider handle. | Low | 📝 Residual — the shared list is ready to wire into `populate_user`; volume risk is tiny (provider handles are pre-owned). |

### Deliberately NOT done (and why)

- **No permanent handle squatting** — reservations expire; names are not NFTs.
- **No rename amnesty for admins** — admins use the same 5 Whys-tested path
  (`rename_user`), not a raw `user.username = ...` write.
- **No external/custom font uploads** — CSP, PWA offline, and privacy (chain 4.3).

## Files

- `users/rename.py` — the rules (only writer of usernames + styles)
- `users/models.py` — `UsernameHistory`, `Profile.name_*` fields, `NAME_*`
  whitelists, safe renderers, money constants, ledger reasons
- `users/forms.py` — `RenameForm`, `NameStyleForm`, reserved names at signup
- `users/views.py` — rename/restyle views, settings identity panel,
  profile redirect for old names
- `users/migrations/0018_*` — schema
- `users/test_rename.py` — 36 tests (money, cooldown, reservation,
  impersonation, validation, redirect, rendering safety)
- `templates/users/_styled_name.html`, `settings.html`, `profile.html`,
  `templates/gallery/feed.html`, `static/gallery/css/blaqvibes.css`
