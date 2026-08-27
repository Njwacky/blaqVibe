"""Dependency audits that cannot execute, resolve or fetch what the uploader wrote.

5 Whys: why a dedicated module instead of `subprocess.run(['pip-audit'], cwd=root)`?
1. Why was the old call dangerous? `cwd=root` put the tool's working directory
   INSIDE the attacker's extracted ZIP. pip-audit with no arguments auto-detects
   that `requirements.txt`, then resolves it — a resolution step that is
   `pip install --dry-run` in a throwaway venv. An uploaded manifest carrying
   `--index-url http://attacker/`, `file:///app/…` or `-e .` therefore made the
   worker send requests wherever the uploader pointed it and BUILD source
   distributions, i.e. run `setup.py` from a package the attacker chose.
   `npm audit` reads the project's own `.npmrc`, so `registry=http://…` in an
   uploaded file redirected the audit the same way.
2. Why does that matter more here than elsewhere? The scan runs in the Celery
   worker, whose environment holds DATABASE_URL, REDIS_URL, the R2 keys and
   PAYSTACK_SECRET_KEY. One upload becomes every credential in the deployment.
3. Why not just sandbox the worker instead? Also right, and out of reach from a
   pull request: the fix must hold on today's compose file. Defence in depth —
   this module removes the capability, the container should still be network-
   namespaced.
4. Why keep the audits at all if we cannot trust the manifest? Because the
   evidence is what the ✓ badge is made of (see `gallery/trust._deps_check`). We
   keep the signal by re-deriving a SAFE input ourselves: exact `name==version`
   pins only, our own index/registry, `--disable-pip`/`--no-deps` so nothing is
   resolved or built, and a clean isolated directory holding only our files.
5. Why fail to `ran: False` instead of guessing? A missing tool, an unreadable
   manifest or a manifest we refused must not look like a passed check — the
   grader then says 'scanned', never 'verified'. Honesty over coverage.

Nothing here raises, nothing here installs, and nothing here reads a config file
from the tree under scan.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# A manifest line we are willing to hand to a resolver at all: exact pin, no
# flags, no URLs, no path/editable references, no ranges.
_PYPI_PIN = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]{0,199})==([A-Za-z0-9][A-Za-z0-9._+!-]{0,99})$')
_NPM_NAME = re.compile(r'^(?:@[A-Za-z0-9._-]{1,39}/)?[A-Za-z0-9._-]{1,214}$')
_NPM_VERSION = re.compile(r'^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-+]{1,60})?$')

MAX_MANIFEST_BYTES = 512 * 1024
MAX_PINS = 400
MAX_RESULTS = 10
AUDIT_TIMEOUT = int(os.getenv('SCAN_AUDIT_TIMEOUT', '45'))

# Our own endpoints. Never derived from the scanned tree.
PYPI_JSON_ROOT = 'https://pypi.org/'
NPM_REGISTRY = 'https://registry.npmjs.org/'
# Directories that are never the project's real manifest, only dependency noise.
_SKIP_DIRS = {'node_modules', '.git', '__pycache__', '.venv', 'venv', '.npm-cache'}


def tools_enabled() -> bool:
    """Set SCAN_AUDIT_TOOLS=0 to skip the audit tools entirely (still honest)."""
    return os.getenv('SCAN_AUDIT_TOOLS', '1').strip().lower() not in ('0', 'false', 'no', 'off')


# --- manifest discovery ------------------------------------------------------

def find_manifests(root):
    """(package_json, package_lock, requirements_txt) — first of each, capped.

    The walk is depth-capped and skips dependency folders: a vendored
    node_modules inside the ZIP is not the project's manifest, and walking it
    costs the 120 s task budget for nothing.
    """
    pkg = lock = req = None
    root = Path(root)
    for dirpath, dirnames, files in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if depth > 4:
            dirnames[:] = []
        if pkg is None and 'package.json' in files:
            pkg = os.path.join(dirpath, 'package.json')
        if lock is None and 'package-lock.json' in files:
            lock = os.path.join(dirpath, 'package-lock.json')
        if req is None and 'requirements.txt' in files:
            req = os.path.join(dirpath, 'requirements.txt')
        if pkg and lock and req:
            break
    return pkg, lock, req


def _read_limited(path):
    try:
        if os.path.getsize(path) > MAX_MANIFEST_BYTES:
            return None
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            return fh.read(MAX_MANIFEST_BYTES + 1)[:MAX_MANIFEST_BYTES]
    except OSError:
        return None


def safe_pip_pins(requirements_path):
    """Exact `name==version` pins from an untrusted requirements.txt.

    Returns None when the file exists but cannot be reduced to pins we trust
    (flags, URLs, editables, ranges) — the caller then reports `ran: False`
    rather than pretending the audit covered it.
    """
    text = _read_limited(requirements_path)
    if text is None:
        return None
    pins, ignored = [], 0
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith('-'):
            # `-r other.txt`, `-e .`, `--index-url …`, `--extra-index-url …`:
            # never followed, never passed on. An @-include too.
            ignored += 1 if line else 0
            continue
        line = line.split(';', 1)[0].strip()          # drop env markers
        match = _PYPI_PIN.match(line)
        if match is None:
            ignored += 1
            continue
        pins.append(f'{match.group(1)}=={match.group(2)}')
        if len(pins) >= MAX_PINS:
            break
    if not pins:
        return None
    return pins


def safe_npm_deps(package_json_path):
    """(name, version) pairs pinned in package.json's dependency blocks.

    Only exact semver is kept. `file:`, `link:`, `git+…`, `npm:alias` and ranges
    are dropped: resolving those is how an audit becomes an install.
    """
    text = _read_limited(package_json_path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    out, skipped = [], 0
    for section in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for name, version in block.items():
            if not isinstance(name, str) or not isinstance(version, str):
                skipped += 1
                continue
            if not _NPM_NAME.match(name) or not _NPM_VERSION.match(version.strip()):
                skipped += 1
                continue
            out.append((name, version.strip()))
            if len(out) >= MAX_PINS:
                return out
    if not out:
        return None
    return out


# --- isolated execution ------------------------------------------------------

def _clean_env(isolated: str) -> dict:
    """A config-free environment: HOME, npmrc and pip conf point at our temp dir.

    The scanned tree is never reachable from any of these paths, so a leftover
    `.npmrc` / `.pypirc` / `pip.conf` in an upload cannot redirect the tool.
    """
    return {
        'PATH': os.defpath + ':/usr/local/bin:/usr/bin:/bin',
        'HOME': isolated,
        'TMPDIR': isolated,
        'LANG': 'C.UTF-8',
        'LC_ALL': 'C.UTF-8',
        'NPM_CONFIG_USERCONFIG': os.path.join(isolated, 'npmrc'),
        'NPM_CONFIG_GLOBALCONFIG': os.path.join(isolated, 'npmrc'),
        'NPM_CONFIG_CACHE': os.path.join(isolated, 'npm-cache'),
        'PIP_CACHE_DIR': os.path.join(isolated, 'pip-cache'),
        'PIP_CONFIG_FILE': os.path.join(isolated, 'pip.conf'),
        'PYTHONUNBUFFERED': '1',
    }


def _prepare_isolation():
    """Our own scratch dir, with the neutral config files npm/pip will read."""
    isolated = tempfile.mkdtemp(prefix='bv-audit-')
    Path(isolated, 'npmrc').write_text(
        f'registry={NPM_REGISTRY}\n'
        'ignore-scripts=true\n'
        'audit=false\n'
        'fund=false\n'
        'update-notifier=false\n'
        'save=false\n'
        'package-lock=false\n',
        encoding='utf-8',
    )
    # Empty on purpose: pip reads it and finds no index-url, no extra-index.
    Path(isolated, 'pip.conf').write_text('[global]\nindex-url = https://pypi.org/simple\n', encoding='utf-8')
    return isolated


def _run(cmd, isolated, extra_env=None):
    env = _clean_env(isolated)
    env.update(extra_env or {})
    return subprocess.run(
        cmd, cwd=isolated, capture_output=True, timeout=AUDIT_TIMEOUT, env=env,
    )


def audit_pip(pins, isolated):
    """Vulnerable package names among `pins`. (name|None, ran, reason)."""
    if not pins:
        return [], False, 'no_manifests'
    req_file = Path(isolated, 'pinned-requirements.txt')
    req_file.write_text('\n'.join(pins) + '\n', encoding='utf-8')
    cmd = [
        'pip-audit', '-r', str(req_file),
        # --no-deps: no resolution, so nothing is downloaded or built.
        # --disable-pip: metadata comes from PyPI's JSON API, not pip.
        '--no-deps', '--disable-pip',
        '--cache-dir', str(Path(isolated, 'pa-cache')),
        '-f', 'json',
    ]
    try:
        result = _run(cmd, isolated, {'PIP_INDEX_URL': PYPI_JSON_ROOT + 'simple/'})
    except FileNotFoundError:
        return [], False, 'tool_missing'
    except subprocess.TimeoutExpired:
        return [], False, 'timeout'
    except Exception:
        logger.exception('pip-audit invocation failed')
        return [], False, 'failed'
    if result.returncode not in (0, 1):
        # 0 = clean, 1 = advisories found; anything else is the tool failing.
        return [], False, f'exit_{result.returncode}'
    try:
        data = json.loads((result.stdout or b'[]').decode('utf-8', errors='ignore') or '[]')
    except ValueError:
        return [], False, 'unparsable'
    deps = data.get('dependencies') if isinstance(data, dict) else data
    if deps is None:
        # An empty payload is what a tool that never looked at anything prints.
        return [], False, 'unparsable'
    names = []
    for dep in deps or []:
        try:
            if dep.get('vulns'):
                names.append(str(dep.get('name')))
        except AttributeError:
            continue
    return names[:MAX_RESULTS], True, 'ok'


def audit_npm(package_json_path, lock_path, isolated):
    """Vulnerable package names for an npm project, without touching its files.

    We copy NOTHING from the tree except a sanitised `package.json` we wrote
    ourselves from the exact pins; `npm audit` is then run against our
    directory with our `.npmrc`. If the project has no lockfile we do not ask
    npm to build a tree from an untrusted manifest — the caller gets ran=False.
    """
    pins = safe_npm_deps(package_json_path) if package_json_path else None
    if not pins:
        return [], False, 'no_manifests'
    if not lock_path:
        # `npm install`-shaped resolution is exactly what we refuse to do.
        return [], False, 'no_lockfile'
    audit_dir = Path(isolated, 'npm-project')
    audit_dir.mkdir(parents=True, exist_ok=True)
    deps = {name: version for name, version in pins}
    (audit_dir / 'package.json').write_text(json.dumps(
        {'name': 'blaqvibes-scan', 'version': '0.0.0', 'dependencies': deps},
        indent=2,
    ), encoding='utf-8')
    # Copy the lockfile only after checking it parses and pins registry hosts we
    # do not use; `--package-lock-only` reads it, never installs from it.
    lock_text = _read_limited(lock_path)
    if lock_text is None:
        return [], False, 'unparsable'
    try:
        json.loads(lock_text)
    except (ValueError, TypeError):
        return [], False, 'unparsable'
    Path(audit_dir, 'package-lock.json').write_text(lock_text[:MAX_MANIFEST_BYTES], encoding='utf-8')
    try:
        result = _run(
            ['npm', 'audit', '--json', '--package-lock-only', '--ignore-scripts',
             '--no-audit', '--no-fund', '--userconfig', os.path.join(isolated, 'npmrc')],
            str(audit_dir),
        )
    except FileNotFoundError:
        return [], False, 'tool_missing'
    except subprocess.TimeoutExpired:
        return [], False, 'timeout'
    except Exception:
        logger.exception('npm audit invocation failed')
        return [], False, 'failed'
    if result.returncode not in (0, 1):
        # 1 is 'advisories found', 0 is clean; 2+ (EBADLOCK, ENOTFOUND, ...) is a
        # failed audit. Silence is never reported as a pass.
        return [], False, f'exit_{result.returncode}'
    try:
        data = json.loads((result.stdout or b'{}').decode('utf-8', errors='ignore') or '{}')
    except ValueError:
        return [], False, 'unparsable'
    if not isinstance(data, dict):
        return [], False, 'unparsable'
    vulns = data.get('vulnerabilities')
    if not isinstance(vulns, dict):
        vulns = data.get('advisories') if isinstance(data.get('advisories'), dict) else None
    if vulns is None:
        # No report object at all: npm answered with an error payload.
        return [], False, 'unparsable'
    names = [str(name) for name in list(vulns.keys())[:MAX_RESULTS]]
    if not names and not isinstance(data.get('metadata'), dict):
        # A clean answer with no metadata block is what `npm audit` prints when
        # it never resolved a tree (empty lockfile, no network). Calling that a
        # pass would hand a project an 'audited, 0 known CVEs' tick for free.
        return [], False, 'empty_report'
    return names, True, 'ok'


def run_dep_audits(extract_root):
    """{npm, pip, dep_audit} for a freshly extracted project tree.

    `dep_audit.ran` is True only when an audit really executed against pins we
    derived ourselves — never when a tool was fed the project's own manifest.
    """
    out = {'npm': [], 'pip': [], 'dep_audit': {'ran': False, 'reason': 'no_manifests'}}
    if not tools_enabled():
        out['dep_audit']['reason'] = 'skipped'
        return out
    pkg, lock, req = find_manifests(extract_root)
    if not (pkg or req):
        return out
    isolated = _prepare_isolation()
    try:
        pip_names, pip_ran, pip_reason = audit_pip(safe_pip_pins(req) if req else None, isolated)
        npm_names, npm_ran, npm_reason = audit_npm(pkg, lock, isolated)
        out['pip'] = pip_names
        out['npm'] = npm_names
        reasons = []
        for ran, reason in ((pip_ran, pip_reason), (npm_ran, npm_reason)):
            if ran:
                reasons.append('ok')
            elif reason not in ('no_manifests',) or not reasons:
                reasons.append(reason)
        out['dep_audit'] = {
            'ran': bool(reasons.count('ok')),
            'reason': 'ok' if 'ok' in reasons else (reasons[-1] if reasons else 'no_manifests'),
        }
        return out
    finally:
        shutil.rmtree(isolated, ignore_errors=True)
