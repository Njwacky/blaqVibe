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

each Why carries 4 points; any point that fails has a
documented fallback approach: degrade, never block, never lie.

WHY 2 — Why a token bucket + per-project cap + cache on the calls?
WHY 3 — Why is a network failure treated as "exists" (fail-open)?
WHY 4 — Why flag in scan_report instead of blocking the publish?
WHY 5 — Why parse manifests ourselves instead of trusting the audit tool
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

# Budget & guards
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

# Manifest parsers (pure, never raise)

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

# Entry point

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
