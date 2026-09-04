"""Content-level diff between two project ZIPs for PR review.

Backend only — never runs in JS. Replaces the old "path set" diff so a PR
actually shows what changed inside files, not just which files exist.
"""
import difflib
import logging
import zipfile

logger = logging.getLogger(__name__)

MAX_DIFF_FILES = 10          # cap common-file content diffs to keep page small
MAX_FILE_BYTES = 512 * 1024  # skip binary / huge files
MAX_DIFF_LINES = 400         # cap unified diff lines per file

def _read_text(zf, name):
    try:
        data = zf.read(name)
        if len(data) > MAX_FILE_BYTES:
            return None
        return data.decode('utf-8', errors='replace')
    except (KeyError, zipfile.BadZipFile, OSError):
        return None

def _as_lines(text):
    return text.splitlines() if text is not None else None

def _cap(lines, limit=MAX_DIFF_LINES):
    if len(lines) <= limit:
        return lines, False
    return lines[:limit], True

def diff_file_content(source_zip_path, target_zip_path, path, added, removed, common):
    """Return a dict describing content changes for the given file path."""
    result = {
        'path': path,
        'status': 'unchanged',
        'additions': 0,
        'deletions': 0,
        'lines': [],
        'truncated': False,
    }
    src = tgt = None
    try:
        with zipfile.ZipFile(source_zip_path) as zs:
            if path in zs.namelist():
                src = _read_text(zs, path)
        with zipfile.ZipFile(target_zip_path) as zt:
            if path in zt.namelist():
                tgt = _read_text(zt, path)
    except (zipfile.BadZipFile, OSError):
        return result

    if src is None and tgt is None:
        return result

    src_lines = _as_lines(src)
    tgt_lines = _as_lines(tgt)

    if src_lines is None and tgt_lines is None:
        return result

    if src_lines is None:
        result['status'] = 'added'
        lines, truncated = _cap(['+' + ln for ln in tgt_lines])
        result['lines'] = lines
        result['additions'] = len(tgt_lines)
        result['truncated'] = truncated
        return result

    if tgt_lines is None:
        result['status'] = 'removed'
        lines, truncated = _cap(['-' + ln for ln in src_lines])
        result['lines'] = lines
        result['deletions'] = len(src_lines)
        result['truncated'] = truncated
        return result

    # PR direction: target (original) -> source (fork). "a/" is the current
    # target content, "b/" is what it becomes after the merge.
    sm = difflib.SequenceMatcher(a=tgt_lines, b=src_lines, autojunk=False)
    unified = list(difflib.unified_diff(
        tgt_lines, src_lines, fromfile=f'a/{path}', tofile=f'b/{path}',
        lineterm='', n=2,
    ))
    additions = sum(1 for ln in unified if ln.startswith('+') and not ln.startswith('+++'))
    deletions = sum(1 for ln in unified if ln.startswith('-') and not ln.startswith('---'))

    result['status'] = 'modified' if (additions or deletions) else 'unchanged'
    result['additions'] = additions
    result['deletions'] = deletions
    lines, truncated = _cap(unified)
    result['lines'] = lines
    result['truncated'] = truncated
    return result

def diff_projects(source, target):
    """Diff the ZIP contents of two projects into a renderable structure.

    Returns a dict with ``added``, ``removed``, ``modified`` (list of
    path/line-diff dicts) plus ``common`` (paths shared but unchanged or
    binary) so the template can still show the full file picture.
    """
    from .models import AppFile

    def flat_files(proj):
        if proj.files.exists():
            return list(proj.files.values_list('path', flat=True))
        # Flatten file_tree dict as a fallback
        out = []

        def walk(d, prefix=''):
            for k, v in (d or {}).items():
                if v is None:
                    out.append(prefix + k)
                else:
                    walk(v, prefix + k + '/')

        walk(proj.file_tree or {})
        return out

    src_files = set(flat_files(source))
    tgt_files = set(flat_files(target))

    added_paths = sorted(src_files - tgt_files)
    removed_paths = sorted(tgt_files - src_files)
    common_paths = sorted(src_files & tgt_files)

    added = []
    removed = []
    modified = []
    unchanged = []

    if source.zip_file and target.zip_file:
        src_path = getattr(source.zip_file, 'path', None)
        tgt_path = getattr(target.zip_file, 'path', None)
        if src_path and tgt_path:
            for path in added_paths:
                added.append(diff_file_content(src_path, tgt_path, path, True, False, False))
            for path in removed_paths:
                removed.append(diff_file_content(src_path, tgt_path, path, False, True, False))
            for path in common_paths[:MAX_DIFF_FILES]:
                d = diff_file_content(src_path, tgt_path, path, False, False, True)
                if d['status'] == 'modified':
                    modified.append(d)
                else:
                    unchanged.append(d)

    # Any common files beyond the diff cap are reported as unchanged.
    remaining = common_paths[MAX_DIFF_FILES:]
    unchanged.extend({'path': p, 'status': 'unchanged', 'additions': 0,
                      'deletions': 0, 'lines': [], 'truncated': False}
                     for p in remaining)

    return {
        'added': added,
        'removed': removed,
        'modified': modified,
        'unchanged': unchanged,
        'added_count': len(added_paths),
        'removed_count': len(removed_paths),
        'modified_count': len(modified),
        'common_count': len(common_paths),
    }
