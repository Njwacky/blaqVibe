"""Storage-agnostic access to project ZIPs.
"""
import contextlib
import os
import shutil
import tempfile
import zipfile

def _local_path(file_field):
    """The real filesystem path, or None on remote storage."""
    try:
        return file_field.path
    except (NotImplementedError, ValueError, AttributeError):
        return None

@contextlib.contextmanager
def open_zip(file_field):
    """Yield a zipfile.ZipFile for a FileField on ANY storage backend.

    Local disk opens in place; remote storage streams through the storage
    API. The caller never sees the difference.
    """
    local = _local_path(file_field)
    if local is not None:
        with zipfile.ZipFile(local) as zf:
            yield zf
        return
    file_field.open('rb')
    try:
        with zipfile.ZipFile(file_field) as zf:
            yield zf
    finally:
        file_field.close()

@contextlib.contextmanager
def materialized_path(file_field, suffix='.zip'):
    """Yield a real filesystem path for tools that need one (clamscan).

    On local storage this is the original path (no copy). On remote
    storage the object is streamed to a NamedTemporaryFile that is
    deleted when the block exits.
    """
    local = _local_path(file_field)
    if local is not None and os.path.exists(local):
        yield local
        return
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        file_field.open('rb')
        try:
            shutil.copyfileobj(file_field, tmp, length=1024 * 1024)
        finally:
            file_field.close()
        tmp.close()
        yield tmp.name
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

def build_tree(file_field):
    """(tree_dict, file_list) from a ZIP FileField on any storage."""
    file_list = []
    tree = {}
    with open_zip(file_field) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            path = info.filename
            file_list.append({'path': path, 'size': info.file_size})
            parts = path.strip('/').split('/')
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = None
    return tree, file_list
