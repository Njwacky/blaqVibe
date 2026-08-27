# BlaqVibes — Trust Badge Spec

**Status:** implemented · **Module:** `gallery/trust.py` · **Public page:** `/trust/`

One sentence: the scan pipeline already proved a vibe was safe — the badge
publishes that verdict on the card, the detail page, and the API, in a way
nobody can fake, buy, or inherit for unscanned bytes.

This spec follows the house rule: every Why carries **4 points**, and every
point that can fail has a **documented fallback approach** — degrade, never
break, never lie.

---

## 1. The tiers

| Tier | Card shows | Means | Written when |
|---|---|---|---|
| `verified` | 🛡️ Checked (green) | Virus scan clean · no leaked secrets · dependencies audited | Published + all three checks pass with evidence |
| `scanned` | 🛡️ Scanned (grey) | Pipeline ran; at least one check incomplete (scanner off, audit tool missing, flagged deps) | Published + evidence exists + a check not ok |
| `unknown` | nothing | No complete evidence (pending, quarantined, removed, legacy) | Everything else — absence IS the signal |

The grade is a **pure function** of `(status, scan_report, zip presence,
snippet code)` — `trust.trust_grade(project)` — never raises, never writes.

## 2. The five rules (5 Whys, 4 points each, fallbacks included)

The full 5×4 text lives as the docstring of `gallery/trust.py` (single
source of truth). Summary:

1. **Derived grade, never raw output.** `scan_report` stays backend-only;
   the badge is a projection through a key whitelist. *Fallback:* a new
   report key is invisible to the grader until deliberately wired.
2. **Stored on the row.** Same reasons `kind`/`appeal_score` are stored:
   indexable, auditable (`trust_graded_at`), cheap to render.
   *Fallback:* a stale row is bounded by the FIFO scan queue depth.
3. **One writer.** Only `apply_trust_grade` (pipeline) and
   `invalidate_trust` (content change) may write `project.trust`. Forms
   use allowlists, the API is read-only, templates never POST it.
   *Fallback:* out-of-order tasks are refused by a monotonic
   `trust_graded_at` guard.
4. **Content change ⇒ reset.** edit / git push / PR merge all call
   `invalidate_trust` on their way to `status='pending'`. A buyer who
   paid stars can never be shown a ✓ describing the *previous* ZIP.
   *Fallback:* a crashed rescan leaves `unknown` — no badge, no lie.
5. **Ranking boost ≤ 8%.** `verified` ×1.08, `scanned` ×1.03, applied to
   the appeal base before the freshness multiplier. Reorders equals;
   cannot buy rank (tested: strong-unknown > weak-verified).
   *Fallback:* it is a pure dict — retune in one place, tests follow.

## 3. Evidence producers (the "pipeline" for both shapes)

| Shape | Steps | Evidence keys |
|---|---|---|
| ZIP | queued chain `clamav → secrets → dep audits → publish` | `clamav`, `secrets`, `npm`, `pip`, `dep_audit`, (`unknown_deps` future) |
| Snippet | in-request `snippet_evidence()` at publish/review: pure `SECRET_PATTERNS` sweep + vacuous dep check | `snippet_scan` |

- `dep_audit = {ran, reason}` is written TRUE only when an audit actually
  executed — a missing tool or manifest can never masquerade as a pass.
- Snippet secrets are re-checked **live at grade time** (defense in depth:
  stale evidence cannot upgrade a token-leaking snippet).
- Moderator approval is also a publish path and re-grades from evidence.

## 4. Anti-spoof / anti-robbery guarantees (and the test that proves each)

| Guarantee | Test |
|---|---|
| POSTing `trust=verified` does nothing; evidence decides | `test_posted_trust_value_is_ignored` |
| No `trust` field exists on any form | `test_publish_form_has_no_trust_field` |
| Content change drops a earned ✓ before any new trade | `test_content_change_resets_a_verified_badge` |
| Buyers see no badge after the swap | `test_buyer_sees_no_badge_after_content_change` |
| Old task cannot overwrite a newer verdict | `test_stale_clock_cannot_overwrite_a_newer_grade` |
| Grading never crashes a publish | `test_grade_is_pure_and_never_raises` |
| API exposes the tier string, never the report | `test_api_returns_tier_never_the_report` |
| Badge copy comes from a fixed server table | `test_meta_table_covers_exactly_the_tiers` |
| Tooltips/reasons never contain filenames or secrets | `test_reasons_are_fixed_strings_and_never_leak_filenames` |
| Boost can't buy rank | `test_boost_is_small_enough_that_quality_still_wins` |

## 5. Surfaces

- Feed card chip (`templates/gallery/feed.html`) — a `<span>`, not a link
  (the card is one `<a>`); `unknown` renders nothing.
- Detail banner (`templates/gallery/app_detail.html`) — tier + per-check
  reasons from `trust_reasons()` + link to `/trust/`.
- `/trust/` (`templates/gallery/trust_legend.html`) — the public standard:
  what each tier promises, what is checked, **what is not** (we do not run
  your code), and how fakes are handled. The page reads `TRUST_META` and
  live counts, so the copy cannot drift from the code.
- API `/api/v1/apps/` — `trust` + `trust_label` keys (add-only change).

## 6. Future work this design already reserves

- `unknown_deps` report key → the slopsquatting check slots into
  `_deps_check` with zero schema changes and instantly tightens `verified`.
- A "verified only" feed filter needs only the `trust` db_index it already
  has.
- A moderator override would write provenance beside the tier; the
  writer rule stays intact because it goes through the same function.
