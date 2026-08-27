# BlaqVibes — Dependency Existence Check Spec (slopsquatting defence)

**Status:** implemented · **Module:** `gallery/dep_check.py` · **Hook:** `gallery/tasks.py vulnerability_scan`

One sentence: AI-generated code invents package names, attackers register
those names with malware ("slopsquatting"), so the scan pipeline now asks
the real registry whether every dependency in a manifest actually exists —
and a 404 caps the vibe's trust tier at `scanned` with a public reason.

House rule: every Why carries **4 points**, each with a documented
**fallback approach** — degrade, never block, never lie. The full 5×4 text
is the docstring of `gallery/dep_check.py` (single source of truth).

---

## 1. What it does

At upload (and every rescan — edit, git push, PR merge), while the ZIP is
already extracted for audits:

1. Parse `package.json` (dependencies, devDependencies, peerDependencies,
   optionalDependencies) and/or `requirements.txt` (conservative name
   extraction: comments, `-r`/`-e`/options and environment markers skipped).
2. For each name (max **20 per project**), ask the registry:
   - npm: `HEAD https://registry.npmjs.org/<name>`
   - PyPI: `HEAD https://pypi.org/pypi/<normalised-name>/json` (PEP 503)
3. **Only an explicit 404 counts as "does not exist."** Everything else —
   200, 403, 5xx, timeout, DNS failure — counts as "exists". A registry
   hiccup can never produce a false "fake package" accusation.
4. Results land in `scan_report`:
   - `unknown_deps: ['npm:suspicious-name', ...]` (flagged, capped at 10)
   - `dep_exist_check: {checked: n, reason: ok|disabled|no_deps|offline|budget|capped}`

## 2. Guarantees

| Property | How |
|---|---|
| Fail-open | Network failure ⇒ treated as existing; circuit breaker ends the run on the first network error (no 20×timeout queue stall) |
| Bounded cost | Global token bucket (`DEP_CHECK_BUDGET`, default 120/hour) + 24h per-name cache + 20/project cap |
| No false blocks | Flags, never quarantines — publishing continues; a moderator sees the flag, buyers see the capped badge |
| Badge reacts | `gallery.trust._deps_check` reads `unknown_deps` ⇒ tier caps at `scanned`, detail page says "Dependency not found on the registry (possible fake package)" |
| Kill switch | `DEP_CHECK_ENABLED=0` disables cleanly (reason `disabled`) |

## 3. Why 404-only is the right bar

A false positive is a **public accusation** against a real creator (private
registries, brand-new publishes, typos-that-exist). A false negative merely
pauses one check while the virus scan, secrets scan and audits still run.
Asymmetric harm ⇒ ambiguity must resolve to "exists".

## 4. Tests (`gallery/tests.py: DepCheckTests`)

- Parsers: all four npm sections; broken JSON → `[]`; requirements
  comments/options/markers/editables skipped
- Registry: 24h cache (one HTTP call per name); only 404 flags;
  200/403/500/None never flag
- Breakers: first network error ends the run; dry budget makes zero calls;
  env switch makes zero calls
- End-to-end through the real task: fake npm dep and fake pip dep get
  flagged, real deps do not, and the trust tier caps at `scanned` once
  published, with the reason visible on the detail page

## 5. Relationship to the trust badge

This check is the muscle behind the promise `/trust/` already makes ("a
registry check that named packages actually exist"). Before this module
that line was the spec's future work; now the code matches the page. See
`BlaqVibes_Trust_Badge_Spec.md` § 6 — `unknown_deps` is now produced, not
merely reserved.
