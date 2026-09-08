"""Phase 5 — ship-readiness as precise checks, never a security guarantee."""
from .trust import _pipeline_ran

_TEST_MARKERS = (
    'test_', '_test.', '.spec.', '.test.', '/tests/', '/test/',
    'pytest', 'phpunit', 'unittest', '_spec.',
)


def _walk_tree(node, prefix=''):
    paths = []
    if isinstance(node, dict):
        for key, child in node.items():
            path = f'{prefix}{key}' if not prefix else f'{prefix}/{key}'
            paths.append(path)
            paths.extend(_walk_tree(child, path))
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, str):
                paths.append(item)
            else:
                paths.extend(_walk_tree(item, prefix))
    elif isinstance(node, str):
        paths.append(node)
    return paths


def file_paths(project):
    cached = []
    try:
        cache = getattr(project, '_prefetched_objects_cache', None) or {}
        cached = [f.path for f in cache.get('files', [])]
    except Exception:
        cached = []
    if cached:
        return cached
    try:
        related = list(project.files.all()[:400])
        if related:
            return [f.path for f in related]
    except Exception:
        pass
    return _walk_tree(getattr(project, 'file_tree', None) or {})


def tests_detected(project):
    hits = []
    for path in file_paths(project):
        lower = path.lower().replace('\\', '/')
        if any(mark in lower for mark in _TEST_MARKERS):
            hits.append(path)
            if len(hits) >= 20:
                break
    return hits


def vuln_count(project):
    report = project.scan_report if isinstance(project.scan_report, dict) else {}
    if report.get('vuln_count') is not None:
        try:
            return int(report['vuln_count'])
        except (TypeError, ValueError):
            pass
    n = 0
    for key in ('npm', 'pip', 'vulnerabilities', 'unknown_deps'):
        val = report.get(key)
        if isinstance(val, list):
            n += len(val)
        elif isinstance(val, dict):
            n += len(val)
    return n


def ship_readiness(project):
    report = project.scan_report if isinstance(project.scan_report, dict) else {}
    tests = tests_detected(project)
    vulns = vuln_count(project)
    scan_ran = _pipeline_ran(report) or project.trust in ('verified', 'scanned')
    secrets = report.get('secrets') or []
    if isinstance(secrets, dict):
        secrets_n = len(secrets)
    else:
        secrets_n = len(secrets) if secrets else 0
    snippet = report.get('snippet_scan') or {}
    if snippet.get('secrets_found'):
        secrets_n = max(secrets_n, 1)
    versions = 0
    try:
        versions = project.versions.count()
    except Exception:
        versions = 0
    events = 0
    try:
        events = project.history.count()
    except Exception:
        events = 0
    return {
        'scan_completed': bool(scan_ran),
        'tests_detected': len(tests),
        'test_paths': tests[:8],
        'vuln_count': vulns,
        'secrets_flagged': secrets_n,
        'trust': project.trust,
        'version_count': versions,
        'event_count': events,
        'disclaimer': (
            'These are precise checks on this object. '
            'BlaqVibes does not guarantee this app is secure.'
        ),
        'rows': [
            {
                'ok': scan_ran,
                'label': 'Scan completed' if scan_ran else 'Scan not completed',
            },
            {
                'ok': len(tests) > 0,
                'label': (
                    f'Tests detected ({len(tests)} file{"s" if len(tests) != 1 else ""})'
                    if tests else 'No test files detected'
                ),
            },
            {
                'ok': vulns == 0 and scan_ran,
                'label': (
                    f'{vulns} known-vulnerable dependenc{"y" if vulns == 1 else "ies"} found'
                    if vulns else 'No known-vulnerable dependencies listed'
                ),
            },
            {
                'ok': secrets_n == 0,
                'label': (
                    f'{secrets_n} secret-shaped hit{"s" if secrets_n != 1 else ""} flagged'
                    if secrets_n else 'No secret-shaped hits in the scan report'
                ),
            },
            {
                'ok': versions > 0 or events > 1,
                'label': (
                    f'Maintenance history: {versions} stored version{"s" if versions != 1 else ""}'
                    f', {events} platform event{"s" if events != 1 else ""}'
                ),
            },
        ],
    }
