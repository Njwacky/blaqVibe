"""Slopsquatting defence — flag dependencies that do not exist on the
registry, because AI-generated code invents package names and attackers
register those names with malware before anyone installs them.

Evidence model: the upload pipeline extracts the ZIP and already finds
package.json / requirements.txt to audit. This module takes the SAME
manifests, lists every dependency name, and asks the real registry
(npmjs.org / pypi.org) whether each name exists. A 404 is the only
"does not exist" verdict — anything else (5xx, timeout, auth wall,
network down) counts as "exists" so a registry hiccup can never smear a
creator's badge with a false "fake package" flag.

The result lands in scan_report['unknown_deps'] (a list like
['npm:definitely-not-real-pkg']) and gallery.trust._deps_check caps the
tier at 'scanned' when it is non-empty. Flag, never quarantine: a
typo'd private package or a brand-new publish must not block publishing.

5 WHYS — each Why carries 4 points; any point that fails has a
documented fallback approach: degrade, never block, never lie.

WHY 1 — Why check existence at all when npm/pip audit already run?
  1. Audits only know REAL packages; a hallucinated name is invisible to
     every audit tool — and it is exactly the name an attacker will
     register tomorrow (slopsquatting).
     Fails-if: an attack registers the name AFTER our check → the check
     runs again on every rescan (edit/push/PR), so re-scans re-ask.
  2. The 63%-non-developer user base cannot spot an invented package by
     eye; the platform holds the manifest at upload — that is the one
     moment it can protect them.
     Fails-if: the user installs from a git clone instead → the badge on
     the site still warns on the detail page ("possible fake package").
  3. Existence is cheap and objective: one HEAD request, one 404 bit —
     no false positives from heuristics about "suspicious names".
     Fails-if: a registry serves a soft-404 page with 200 → we treat it
     as existing (no flag); the failure mode is a missed flag, not a
     wrong one.
  4. It is the check the /trust/ page already promises ("a registry
     check that named packages actually exist") — the code must match
     the published standard or the badge is decoration.
     Fails-if: the page and code drift → the page renders from
     TRUST_META + the spec; drift is a docs bug, not a trust bug.

WHY 2 — Why a token bucket + per-project cap + cache on the calls?
  1. The load target is "tons of uploads every second"; one registry
     request per dependency makes pipeline throughput linear in spam
     volume — the same answer classify.py already gives for LLM calls.
     Fails-if: a spam wave still arrives → the bucket bounds the worst
     case to a constant per hour; rows past it keep reason='budget' and
     lose nothing but the existence check.
  2. Registries rate-limit scrapers; an unbounded checker would get the
     site's IP throttled, turning a defence into an outage.
     Fails-if: we are throttled anyway (429/5xx) → those statuses count
     as "exists" (no flags) and the circuit breaker skips the rest.
  3. A package existing is stable knowledge — caching 24h per name makes
     the common dependency (react, django) cost zero after its first
     check site-wide.
     Fails-if: a name is registered an hour after a cache miss → the
     next rescan of any project using it re-asks after TTL expiry.
  4. Per-project cap (20) stops a single manifest with 2,000 deps from
     eating the whole bucket alone.
     Fails-if: a project exceeds the cap → the first 20 are checked and
     the run is marked reason='capped'; no flag is invented for the rest.

WHY 3 — Why is a network failure treated as "exists" (fail-open)?
  1. A false "fake package" flag on a legit creator is a public accusation
     with no evidence — worse damage than a missed flag on an attacker.
     Fails-if: an attacker ships during an outage → the virus scan, the
     secrets scan and the audits still run; only this one check pauses.
  2. Sandboxes, CI and air-gapped deploys have no registry access; a
     fail-closed checker would flag EVERY dependency there.
     Fails-if: an operator wants strictness → set DEP_CHECK_ENABLED=0 to
     disable cleanly rather than lie; strict mode is a future flag.
  3. Only an explicit 404 says "the registry knows this name is not
     taken"; ambiguity must not become an accusation.
     Fails-if: a registry 404s transiently (it happens) → the rescan on
     the next content change clears the flag; nothing is quarantined.
  4. The circuit breaker (one network error ends the run) keeps a
     blackholed DNS from costing 20 × timeout=5s of queue time.
     Fails-if: the network returns mid-run → the next project's run
     starts fresh; the breaker is per-run, not global.

WHY 4 — Why flag in scan_report instead of blocking the publish?
  1. False positives exist: private registries, brand-new publishes,
     typo'd-but-real names — quarantine would punish real creators on
     guesswork.
     Fails-if: the flag is wrong → the creator edits the manifest or the
     name registers; the rescan clears it automatically.
  2. The house rule is "everything gets published, including things we
     cannot fully check" — the honest response to doubt is a visible
     warning, not a gate.
     Fails-if: the flag is right → the tier caps at 'scanned', the detail
     page names the reason, and buyers see it BEFORE paying stars.
  3. Moderators get signal without a new surface: the flag rides
     scan_report into the existing moderation view.
     Fails-if: moderation needs the exact names → they are in the report
     (backend-only), never rendered raw to the public.
  4. Blocking would move the decision to an automated system with no
     appeal; flagging keeps a human in the loop by default.
     Fails-if: a wave of fake-package uploads arrives → the bucket caps
     spend and the tier cap still applies; nothing auto-deletes.

WHY 5 — Why parse manifests ourselves instead of trusting the audit tool
        output for the dependency list?
  1. npm audit reports VULNERABLE packages only; the hallucinated name
     with zero advisories is precisely the row audit never mentions.
     Fails-if: an advisory exists for a real name → it lands in
     report['npm'] and the tier already caps for 'vulnerable_deps'.
  2. The parse is two pure functions (json.load / line split) with no
     subprocess, no network, no state — testable in one assert each.
     Fails-if: a manifest is malformed → the parser returns [] and the
     run degrades to reason='no_deps'; never an exception.
  3. requirements.txt has no machine contract (comments, options,
     markers, continuation lines) — a conservative name regex extracts
     only what certainly is a name and skips the rest.
     Fails-if: an exotic line is skipped → fewer checks, no false flags.
  4. Parsing at extract time reuses the one moment the ZIP is already
     open on disk; a second extraction just for names would double I/O
     on the hot path.
     Fails-if: the manifest is nested deeper than the walk visits → the
     walk's first-match rule (existing behaviour) applies; fewer checks,
     no wrong ones.
"""
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from django.core.cache import cache

logger = logging.getLogger(__name__)

# --- Budget & guards -------------------------------------------------------
# One window, one counter in the cache. Per-process under locmem, shared
# under Redis — either way the worst case is a small multiple of MAX.
BUCKET_KEY = 'dep_check_bucket'
BUCKET_WINDOW_SECONDS = 3600
DEFAULT_BUDGET = 120          # registry requests per hour, env-tunable
PER_PROJECT_CAP = 20          # names checked per upload
CACHE_TTL_SECONDS = 24 * 60 * 60
HTTP_TIMEOUT = 5

# PEP 503 normalisation for PyPI names (- . _ are equivalent, case-blind).
_PEP503 = re.compile(r'[-_.]+')
# The "certainly a name" prefix of a requirements line.
_REQ_NAME = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)')


def _enabled():
    try:
        return os.getenv('DEP_CHECK_ENABLED', '1') != '0'
    except Exception:
        return True


def _budget_max():
    try:
        return int(os.getenv('DEP_CHECK_BUDGET', str(DEFAULT_BUDGET)))
    except Exception:
        return DEFAULT_BUDGET


def _budget_spend():
    """True if this call may spend one registry request. Window counter."""
    try:
        import time
        now = int(time.time())
        raw = cache.get(BUCKET_KEY) or {'start': now, 'count': 0}
        if now - raw['start'] >= BUCKET_WINDOW_SECONDS:
            raw = {'start': now, 'count': 0}
        raw['count'] += 1
        cache.set(BUCKET_KEY, raw, BUCKET_WINDOW_SECONDS)
        return raw['count'] <= _budget_max()
    except Exception:
        return False  # broken cache → buy nothing, flag nothing


def _http_status(url, timeout=HTTP_TIMEOUT):
    """Thin seam for tests. Returns the HTTP status int, or None on any
    network-level failure (DNS, timeout, refused)."""
    try:
        req = urllib.request.Request(url, method='HEAD',
                                     headers={'User-Agent': 'BlaqVibesDepCheck/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except Exception:
        return None


def _registry_url(eco, name):
    if eco == 'npm':
        return 'https://registry.npmjs.org/' + urllib.parse.quote(name, safe='@/')
    return 'https://pypi.org/pypi/' + urllib.parse.quote(_PEP503.sub('-', name).lower(), safe='') + '/json'


def _exists(eco, name):
    """(exists, offline). exists=False ONLY on an explicit 404; offline=True
    means the network itself failed (circuit-breaker signal). Cache 24h."""
    key = f'dep_exists:{eco}:{name.lower()}'
    try:
        hit = cache.get(key)
        if hit is not None:
            return bool(hit), False
    except Exception:
        pass
    status = _http_status(_registry_url(eco, name))
    exists = status != 404          # None/200/5xx/anything → treat as real
    offline = status is None        # network-level failure → stop the run
    try:
        if not offline:
            cache.set(key, exists, CACHE_TTL_SECONDS)
    except Exception:
        pass
    return exists, offline


# --- Manifest parsers (pure, never raise) ----------------------------------

def npm_deps_from_manifest(path):
    """Dependency names from a package.json (deps, dev, peer, optional)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            data = json.load(fh)
        names = set()
        for section in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
            try:
                names.update((data.get(section) or {}).keys())
            except Exception:
                continue
        return sorted(n for n in names if isinstance(n, str) and n.strip())
    except Exception:
        return []


def pip_deps_from_requirements(path):
    """Conservative name extraction from requirements.txt."""
    try:
        names = set()
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('-'):
                    continue                      # blank / comment / option / -e / -r
                line = line.split(';')[0].strip()  # drop env markers
                m = _REQ_NAME.match(line)
                if m:
                    names.add(m.group(1))
        return sorted(names)
    except Exception:
        return []


# --- Entry point ------------------------------------------------------------

def check_dependencies(deps):
    """Existence-check a project's dependency names.

    deps: {'npm': [...], 'pip': [...]}  →  {'flagged': ['eco:name', ...],
    'checked': int, 'reason': 'ok'|'disabled'|'no_deps'|'offline'|'budget'|'capped'}

    Never raises, never blocks a publish, never invents a flag without an
    explicit registry 404 (WHY 3).
    """
    result = {'flagged': [], 'checked': 0, 'reason': 'ok'}
    try:
        if not _enabled():
            result['reason'] = 'disabled'
            return result
        pairs = []
        for eco, key in (('npm', 'npm'), ('pip', 'pip')):
            for name in (deps or {}).get(key) or []:
                if isinstance(name, str) and name.strip():
                    pairs.append((eco, name.strip()))
        if not pairs:
            result['reason'] = 'no_deps'
            return result
        for eco, name in pairs[:PER_PROJECT_CAP]:
            if not _budget_spend():
                result['reason'] = 'budget'
                break
            exists, offline = _exists(eco, name)
            if offline:
                # Circuit breaker: one network failure ends the run — a
                # blackholed DNS must not cost 20 × timeout of queue time.
                result['reason'] = 'offline'
                break
            if not exists:                      # explicit 404 only
                result['flagged'].append(f'{eco}:{name}')
            result['checked'] += 1
        if result['reason'] == 'ok' and len(pairs) > PER_PROJECT_CAP:
            result['reason'] = 'capped'
        return result
    except Exception:
        logger.exception('dep check failed')
        return result
