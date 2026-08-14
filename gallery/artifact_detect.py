"""Detect what kind of shippable artifact a ZIP contains.

Closes the publish → launch loop: after a vibe is published we look at its
file list and point the creator at the matching launch guide
(/launch/?artifact=<value>).

5 Whys:
1. Why detect at all? After publish, nothing told the creator what to do
   next — the launch hub was a separate island nobody found.
2. Why detect from the stored file list (AppFile / file_tree) and not by
   re-reading the ZIP? The tree is already extracted at upload; reading
   the archive again is wasted I/O and another storage round-trip.
3. Why first-match priority order instead of returning every match?
   A Next.js repo contains package.json AND often a Dockerfile. The
   creator needs ONE next step, not a quiz. Specific beats generic:
   aab > apple > extension > flatpak > container > frontend > pwa > static.
4. Why only look at the top two path segments? ZIPs usually wrap
   everything in one root folder. node_modules/foo/package.json three
   levels down is a dependency, not the project.
5. Why must every returned value exist in ARTIFACT_ROUTES? The value goes
   straight into /launch/?artifact=… — an unknown value silently shows
   nothing. The test suite cross-checks the two tables.
"""
from .launch_guides import ARTIFACT_ROUTES

_ROUTE_VALUES = {route['value'] for route in ARTIFACT_ROUTES}

# (artifact value, matcher) — first hit wins, most specific first.
# Matchers get (basename_lower, full_path_lower) for each candidate file.


def _name_is(*names):
    wanted = set(names)
    return lambda base, path: base in wanted


def _ext_is(*exts):
    suffixes = tuple(exts)
    return lambda base, path: base.endswith(suffixes)


_DETECTORS = (
    ('aab', _ext_is('.aab')),
    ('apple', _ext_is('.xcarchive', '.ipa')),
    ('extension', _name_is('manifest.json')),   # refined below: needs browser keys nearby? keep simple: manifest.json + no index.html handled by order
    ('flatpak', _ext_is('.flatpakref')),
    ('container', _name_is('dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml')),
    ('frontend', _name_is('package.json')),
    ('static', _name_is('index.html', 'index.htm')),
)


def _candidate_paths(project):
    """Lower-cased file paths, shallow entries only (root + one folder)."""
    paths = []
    try:
        for f in project.files.all()[:2000]:
            p = (f.path or '').replace('\\', '/').strip('/').lower()
            if p and p.count('/') <= 1:
                paths.append(p)
    except Exception:
        pass
    if not paths and isinstance(project.file_tree, dict):
        # Fallback: walk the stored tree one level deep.
        for name, child in project.file_tree.items():
            lname = str(name).lower()
            if child is None:
                paths.append(lname)
            elif isinstance(child, dict):
                for sub, subchild in child.items():
                    if subchild is None:
                        paths.append(f'{lname}/{str(sub).lower()}')
    return paths


def detect_artifact(project):
    """Return an ARTIFACT_ROUTES value for this project, or ''.

    Never raises; detection is best-effort decoration on the publish flow.
    """
    try:
        if not project.zip_file:
            # Pure snippets are already "live" in the preview; static-site
            # guidance still fits if there is HTML to host.
            return 'static' if project.html_code else ''
        paths = _candidate_paths(project)
        if not paths:
            return ''
        pairs = [(p.rsplit('/', 1)[-1], p) for p in paths]
        for value, matcher in _DETECTORS:
            if value not in _ROUTE_VALUES:
                continue  # guides table changed; skip silently, tests catch it
            for base, path in pairs:
                if matcher(base, path):
                    # manifest.json alone is ambiguous (PWA vs extension):
                    # call it an extension only when there is no index.html
                    # (a PWA manifest ships beside its page).
                    if value == 'extension':
                        has_index = any(b in ('index.html', 'index.htm') for b, _ in pairs)
                        if has_index:
                            continue
                    return value
        return ''
    except Exception:
        return ''


def artifact_route(value):
    """The full route dict for a detected value, or None."""
    for route in ARTIFACT_ROUTES:
        if route['value'] == value:
            return route
    return None
