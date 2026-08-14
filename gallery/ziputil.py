"""Storage-agnostic access to project ZIPs.

5 Whys:
1. Why does this module exist? Six call sites used `project.zip_file.path`.
   `FieldFile.path` raises NotImplementedError on remote storage (S3/R2),
   so the scan → publish pipeline died in the exact production config the
   storage hardening was built for.
2. Why did nobody notice? Dev and tests always run on local MEDIA_ROOT,
   and the callers wrapped everything in broad `except Exception`.
3. Why two helpers instead of one? Most callers (tree build, secrets scan,
   language detect, file preview) only need to READ the archive —
   zipfile.ZipFile accepts a file object, so `open_zip()` streams straight
   from storage with no temp file. Only external tools that demand a real
   filesystem path (clamscan) need `materialized_path()`.
4. Why a context manager for materialization? A temp copy of a 100 MB ZIP
   that is never cleaned up is a disk-leak per scan. `with` ties the file's
   lifetime to the work.
5. Why keep using .path when it exists? Local disk needs no copy —
   materializing there would double I/O for nothing.
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
