# Language & program-kind detection — deep research and the rewrite it produced

**Problem.** BlaqVibes auto-detects two things when a project is published:
what **languages** it is written in (`gallery/language.py` →
`AppProject.language_stats`) and what **kind of program** it is
(`gallery/kind_detect.py` + `gallery/classify.py` → `AppProject.kind`).
Both kept getting real uploads wrong. Measured on realistic uploads with
the code as it was:

| Upload | Language bar said | Kind said |
| --- | --- | --- |
| Python app that also had `package-lock.json` | **JSON 95%**, Python 5% | api_backend (lucky) |
| React site zipped with `node_modules/` | JavaScript 100% (all of it vendored) | — |
| C++ project (`.cpp`/`.h`/CMakeLists) | **nothing** ({} — extensions unmapped) | — |
| Flutter project | **nothing** (`.dart` unmapped) | mobile_app (via pubspec only) |
| Python repo with `docs/` + `dist/bundle.min.js` | **JavaScript 72%** | — |
| Java Spring Boot server built with Gradle | — | **mobile_app** (wrong) |
| Electron app, package.json only | — | **web_app** (wrong) |
| Discord bot, package.json only | — | **web_app** (wrong) |
| Streamlit dashboard | — | **api_backend**, conf 0.25 (wrong) |
| Go CLI tool | — | **api_backend** (wrong-ish) |

The point of this document is the deep research into how the tools that
get this right actually do it, and the rules that came out of it. The code
changes live in `gallery/language.py`, `gallery/kind_detect.py` and
`gallery/classify.py`; the regression suite is `gallery/test_detection.py`.

---

## 1. How GitHub Linguist detects languages (the bar users compare against)

Linguist is GitHub's own detector — the thing that paints the language bar
on every repo. When a builder says "the language is wrong", they almost
always mean "GitHub says something different". Its algorithm
([repo](https://github.com/github-linguist/linguist), [FAQ #4263](https://github.com/github-linguist/linguist/issues/4263),
[Stack Overflow: how does GitHub figure out a project's language](https://stackoverflow.com/questions/5318580/how-does-github-figure-out-a-projects-language)):

### 1.1 Per-file detection is a strategy chain, first hard answer wins

For every file, in order, **stopping at the first strategy that returns
exactly one language**:

1. Emacs/Vim **modelines** in the file content.
2. **Known filename** — `Makefile`, `Dockerfile`, `Rakefile`, `CMakeLists.txt` …
3. **Shebang** — `#!/usr/bin/env python3` beats any other evidence for an
   extensionless script.
4. **Extension** — `.py`, `.tsx` … the workhorse, but ambiguous for
   `.h` (C/C++/Objective-C), `.pl`, `.m` and friends.
5. **Heuristic rules** — regular expressions over content that disambiguate
   the conflicting extensions.
6. **Naive Bayesian classifier** — trained on sample files, the
   last-resort. Key design point: *the classifier only refines among the
   candidates the earlier strategies produced* — it is never asked to pick
   from all languages.

**What we adopted:** the ordering (filename → shebang → extension) and the
principle that an expensive/uncertain stage only refines candidates the
cheap stages produced. In BlaqVibes the "Bayesian classifier" role is
played by the selective LLM call in `classify.py`, and it now receives the
heuristic's evidence in its prompt for exactly this reason.
**What we skipped:** modelines and the content classifier both require
reading every file; a browser-uploaded zip cannot pay that on the publish
path. The extension map was made generous instead (~90 entries), and the
shebang pass only reads the first 256 bytes of small *extensionless* files
(capped at 200 reads).

### 1.2 Stats are byte-weighted, never file-count-weighted

Linguist sums the **bytes** of each detected file. Our old code already
did this — that part was right.

### 1.3 The exclusions are where "wrong language" comes from

This was the big lesson. Linguist does not just detect — it decides what
NOT to count, and users judge correctness on that:

- **Vendored paths** (`vendor.yml`): `node_modules/`, `bower_components/`,
  `vendor/`, `third-party/`, `dist/`, `deps/`, `env/`, `.yarn/releases/` …
  Checking third-party code into a repo is common and "often inflates your
  project's language stats or may even cause your project to be labeled as
  another language"
  ([overrides doc](https://github.com/github-linguist/linguist/blob/main/docs/overrides.md)).
  Notably linguist does **not** vendor `bin/` or plain `build/` — too many
  real projects keep source there.
- **Generated files** (`generated.rb` rules): `*.min.js`, `*.min.css`,
  source maps, `*.d.ts` bundles — "not all plain text files are true
  source files".
- **Documentation** (`documentation.yml`): `docs/`, `documentation/`,
  `examples/`, `man/`, `README*`, `CHANGELOG*`, `CONTRIBUTING*`,
  `LICENSE*`, … all excluded from the bar.
- **Language type** (`languages.yml` `type:`): only **programming** and
  **markup** languages are counted. **Data** (JSON, YAML, TOML, SQL, XML)
  and **prose** (Markdown, Text) are not — that is why a repo's bar never
  shows Markdown no matter how fat the README is.

**What we adopted:** all four rules, as a curated subset of linguist's
actual lists (the entries that occur in browser-uploaded zips), plus every
package-manager **lockfile** treated as generated data. One deliberate
divergence: when the archive holds *nothing but* data/prose languages, we
show them rather than an empty bar — a degrade, never a lie.
**Regression that this fixes:** `package-lock.json` (1 MB of JSON) can no
longer make a Python project "JSON 95%"; `node_modules/` inside an uploaded
zip can no longer rename the project JavaScript.

### 1.4 GitHub zips add a wrapper folder

`https://codeload.github.com/<owner>/<repo>/zip/…` wraps everything in
`<repo>-<ref>/`. Rules like "docs/ is documentation" are written against
the repo root. `/import/github/` already strips the wrapper; a hand-uploaded
GitHub zip does not get that courtesy, so both detectors now strip a
common single top-level segment before applying any rule.

## 2. enry — the same algorithm, ported

[go-enry](https://github.com/go-enry/go-enry) (Sourcegraph's Linguist port)
makes the architecture explicit: strategies are functions
`(filename, content, candidates) → languages`, tried in order, each one
either deciding, narrowing the candidate set, or passing it on; the
Bayesian classifier runs last over whatever candidates remain. That
"narrow, then decide" shape is exactly what `kind_detect` (cheap
fingerprints, scored) → `classify` (LLM only under the confidence floor)
now mirrors.

## 3. Wappalyzer — dependencies are the fingerprint

[Wappalyzer](https://github.com/tomnomnom/wappalyzer) identifies the
technology behind a website from fingerprints, and its most instructive
idea is the **`implies` relation**: detecting one technology implies
others (WordPress → PHP), and every fingerprint belongs to *categories*
(CMS, framework, analytics…). It never asks "what is this site?" — it asks
"which fingerprints match, and what do those fingerprints imply?"

For source zips the equivalent fingerprint is the **dependency manifest**:
a project that depends on `electron` is a desktop app, one that depends on
`express`/`@nestjs/core` is a backend, `react`/`next`/`vue` is a web app,
`discord.js`/`telegraf` is a bot, `playwright`/`selenium` is a scraper,
`torch`/`transformers` is AI/ML, `streamlit`/`gradio` is a dashboard,
`pygame` is a game. Dependencies cannot be marketing copy — they are load
bearing.

**What we adopted:** `kind_detect._read_manifests()` reads the shallowest
`package.json`, `requirements.txt`, `pyproject.toml`, `Pipfile`,
`pubspec.yaml`, `Cargo.toml`, `go.mod`, `composer.json` or `Gemfile` from
the zip (≤128 KB each, ≤6 files, vendor and lock paths skipped), and
`_manifest_scores()` maps known dependency names to kinds through the
`_NPM_SIGNALS` / `_PYPI_SIGNALS` tables plus small marker tables for
Cargo/go.mod/composer/Gemfile/pubspec. Evidence strings name the trigger
(`package.json uses express`) so the badge stays arguable.

This is what fixed the family of "all web-shaped projects are web_app"
mistakes: Electron, Express, Next.js and a Discord bot all ship a
`package.json`, and only its `dependencies` block tells them apart.

## 4. The one thing no research could save: gradle

`build.gradle` occurs in both Android apps and JVM servers. Any static
weight is wrong half the time — the old `('mobile_app', 4)` made every
Spring Boot service a mobile app. Gradle is now decided by a **combo
rule**: gradle + Android markers (`AndroidManifest.xml`, `android/`,
`MainActivity.*`) → mobile_app; gradle without them → api_backend. This is
the same "narrow with cheap facts, then decide" pattern the rest of the
chain uses.

---

## 5. The rewrite, module by module

| Module | What changed |
| --- | --- |
| `gallery/language.py` | filename map + shebang pass before the extension map; extension table ~22 → ~90 languages; vendor/generated/documentation/lockfile exclusions; programming+markup only counting (data/prose as fallback); GitHub-wrapper strip; per-file byte cap. |
| `gallery/kind_detect.py` | dependency-manifest fingerprints (new); gradle combo rule replacing a wrong static weight; ~40 new name/ext/dir signals (`go.mod`, `pom.xml`, `MainActivity.kt`, `project.pbxproj`, `.svelte`, `.astro`, `cmd/`, …); language-bias table extended to the new language names and kept deliberately weak; GitHub-wrapper strip shared with language.py. |
| `gallery/classify.py` | the LLM prompt now carries the heuristic's detected markers, so the "classifier" stage refines candidates instead of starting from zero. |

### Measured after (same uploads as the table at the top)

| Upload | Language bar now | Kind now |
| --- | --- | --- |
| Python app + `package-lock.json` | Python 100% | api_backend |
| React site with `node_modules/` | JS 99 / HTML 1 (only own files) | web_app |
| C++ project | C++ 91 / CMake 9 | desktop_app |
| Flutter project | Dart 100 | mobile_app |
| Python repo + docs + dist bundle | Python 100 | api_backend |
| Java Spring Boot (gradle) | Java 100 | **api_backend** (was mobile_app) |
| Electron, package.json | — | **desktop_app** `package.json uses electron` |
| Express API, package.json | — | **api_backend** conf 0.85 (was 0.40) |
| Discord bot, package.json | — | **bot** `package.json uses discord.js` |
| Streamlit dashboard | — | **data_viz** conf 0.78 (was api_backend 0.25) |
| Go CLI (cobra in go.mod) | Go 100 | **cli_tool** `go.mod uses spf13/cobra` |
| Selenium scraper | — | **bot** `python deps include selenium` |

Rows that land *just under* the LLM confidence floor (0.55) do so on
purpose: the deterministic answer is written first, and the LLM — when a
key exists — confirms or corrects only the ambiguous ones, capped by the
per-minute budget. No key, no network, no money: the heuristic floor
stands on its own.

## 6. 5 Whys — why these rules and not an ML model or "just ask the LLM"?

1. **The publish path must classify with no API key** — a large share of
   uploads happen with no provider configured; an LLM-only detector has no
   floor under it. The deterministic layer is the product.
2. **Users verify detection against GitHub**, so the rules that produce
   GitHub's answer (exclusions, byte weighting, filename/shebang/extension
   order) are the only rules whose correctness users can predict.
3. **Dependencies beat prose.** Title/README/tech_stack words are written
   to sell; `package.json`'s dependency block is executed by a machine.
   Fingerprints from manifests outrank text words in the evidence chain.
4. **Single ambiguous filenames are undecidable** (gradle, `package.json`,
   `.h`): only a combo rule or manifest content can split them, and where
   neither exists the case must fall through to the LLM/classifier stage
   instead of pretending certainty.
5. **Every signal must argue its case** (`kind_evidence`), because a
   creator who sees "auto-guess was X" plus three named reasons can
   correct it once and trust the system after.

## 7. Regression suite

`gallery/test_detection.py` pins every rule this document claims:

- linguist exclusions (vendor / lockfile / minified / docs / wrapper strip)
- filename, shebang and extension strategies; data-only fallback
- gradle combo both ways; manifest fingerprints per ecosystem
- taxonomy guard: manifest tables may not invent kinds
- LLM prompt carries heuristic markers

## References

- github-linguist/linguist — repo README + `docs/overrides.md` (vendor /
  generated / documentation / detectable rules)
- github-linguist/linguist issue #4263 — the FAQ answer spelling out the
  strategy chain and the stats rules
- go-enry wiki — "Enry, a faster implementation of github linguist in Go"
  (strategies + classifier-over-candidates architecture)
- Wappalyzer specification — fingerprints, categories, `implies`/`requires`
- Stack Overflow: "How does GitHub figure out a project's language?"
  (byte-weighted stats, exclusions, classifier as last strategy)
