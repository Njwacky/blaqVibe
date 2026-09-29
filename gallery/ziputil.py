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

    The FieldFile is left OPEN (and rewound) on the way out — on purpose.
    Closing it here is what turned every publish into a bare 500 on the
    S3/R2-backed deploy:

      * AppProject.save() reads the archive for language stats through
        detect_languages_from_field(), which is the one reader that runs
        while the upload is still unsaved.
      * The very next statement in that same save() is super().save(), and
        writing the row writes the FileField through storage.save().
      * django-storages streams that content to boto3, which reads the file
        object it is handed — already closed — and raises
        "ValueError: I/O operation on closed file" straight out of
        AppProject.save(). No handler catches it, so POST /publish/ and
        POST /import/github/ both answer 500 with no traceback for the user.

    Local disk never hit this because the local-path branch above opens the
    archive by path and never touches the FieldFile, so the regression was
    invisible to the suite that runs on FileSystemStorage.

    Nothing leaks: CPython releases the stream as soon as the last reference
    to the FieldFile goes (end of the request, or when the scan worker moves
    on), and a caller that is finished with the bytes can close the field
    itself.
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
        try:
            file_field.seek(0)
        except Exception:
            pass

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
