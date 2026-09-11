# Publish First — the simplified project publish flow

> **PUBLISH FIRST. BUILD THE PROOF LATER.**

## The problem

The old `/publish/` flow was a 3-step wizard with ~18 inputs. A creator who
had just finished building — the moment they are most excited — had to write
a 100-character README with a `#` heading, classify their program, explain
what AI did, what the human did, name tools, paste prompts, describe remix
deltas, and set pricing before they were allowed to show anything. Many
simply left.

## The new flow

The publish page is **one card with four primary inputs**:

1. **Project name** — `title`
2. **What did you build?** — `short_description` (one line)
3. **Your project** — the existing ZIP picker, with a folded-away
   snippet editor (HTML/CSS/JS). The upload backend is untouched.
4. **How did you build it?** — one tap: Human-built / AI-assisted /
   AI-generated / Remixed.

Then one big **Publish project** button.

Everything else became a post-publish, always-optional step:

| Field | Before | Now |
| --- | --- | --- |
| README | Required, ≥100 chars + heading | Auto-scaffolded, honestly labelled "starter scaffold"; replaced via the strengthen step |
| Category | Required select | Chosen automatically (`repo_import.suggest_category`) |
| `ai_tool` / `ai_prompt` | Required when AI-generated | Invited after publish (proof checklist + success page) |
| `problem_statement`, `human_did`, `ai_got_wrong` | On the form | Edit page only ("Build method & proof" section) |
| Remix fields | On the form | Edit page; enforced only for real fork children |
| `star_cost` / `price_zar` | On the form | Default free; editable on the edit page |

## After publishing

`publish()` redirects to **`/publish/done/<slug>/`** (`publish_success`):

- `PROJECT PUBLISHED ✓` hero (or the honest "safety scan running" /
  "held for review" state for ZIPs and flagged snippets),
- a **PROJECT PROOF** meter driven by the existing `proof_checks()` rows,
- optional strengthen actions (`gallery.proof.strengthen_actions`) that
  deep-link into real pages — edit sections, Builder Skills — and a
  **Maybe later** link straight to the live project.

Nothing on that page is required. The project is already out.

## How the build-method pick is stored

One new nullable-ish column, zero destroyed fields:
`AppProject.build_choice` (migration `0041`), values `'' | human |
ai_assisted | ai_generated`.

`build_method` (the derived label shown everywhere) resolves in this order:

1. `forked_from_id` → `remixed` (a lineage fact, never a claim)
2. `build_choice in (ai_assisted, ai_generated)` → the creator's claim
3. `ai_generated` → `ai_generated`
4. `ai_tool` → `ai_assisted`
5. else `human_built`

So old rows derive exactly as before, a one-tap claim can lift the label,
and recorded evidence (a named tool) can still upgrade it — but nothing can
fake a remix or hide recorded AI use.

`remixed` on the publish form is refused with a direction ("open the
original and tap Remix") because lineage is platform-written.

## What did NOT change

- The upload/scan pipeline, snippet flow, challenge tagging, taste
  recording, moderation fan-out, rate limits.
- `AppUploadForm` — still the full form for the **edit page** (where README
  rules, proof fields and AI details live) and `repo_import`.
- Every database field, migration, and existing project.
- The studio posts through the same publish path with the same four inputs
  (plus an optional README its Nolo helper can fill).

## Files

- `gallery/forms.py` — `QuickPublishForm`, `apply_build_method`, `build_choice` on `AppUploadForm`
- `gallery/views.py` — `publish()` (quick form + success redirect), `publish_success`
- `gallery/proof.py` — README scaffold, strengthen actions, proof levels
- `gallery/urls.py` — `publish/done/<slug>/`
- `templates/gallery/publish.html`, `publish_success.html`, `edit_vibe.html`, `studio.html`
- `static/gallery/js/publish.js`, `static/gallery/css/publish.css`
- `gallery/test_quick_publish.py`, `gallery/test_ai_provenance.py`
