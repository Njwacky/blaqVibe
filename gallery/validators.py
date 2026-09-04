import zipfile, os, re, logging
from django.core.exceptions import ValidationError
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_ZIP_SIZE = getattr(settings, 'BLAQVIBES_MAX_ZIP_MB', 100) * 1024 * 1024
MAX_FILES = getattr(settings, 'BLAQVIBES_MAX_FILES', 1000)
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_FILENAME_LEN = 255
BLOCKED_NAMES = {
    'node_modules', '__pycache__', '.git', 'venv', '.venv', '.env', '__MACOSX',
    '.DS_Store', '.ssh', '.aws',
    '.npmrc', '.yarnrc', '.yarnrc.yml', '.pnpmfile.cjs', '.netrc', '_netrc',
    '.pypirc', '.pip.conf', 'pip.conf', '.gitconfig', '.git-credentials',
    'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
}
BLOCKED_EXT = {'.exe','.dll','.so','.dylib','.sh','.bat','.bin','.o','.a'}
BULK_NAMES = {'node_modules', '__pycache__', 'venv', '.venv', '__MACOSX', '.DS_Store'}
SECRET_PATTERNS = [
    re.compile(r'sk_live_[0-9a-zA-Z]+'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'-----BEGIN (RSA )?PRIVATE KEY-----'),
    re.compile(r'ghp_[A-Za-z0-9_]{36}'),
]


def _is_symlink(zip_info):
    try:
        return (zip_info.external_attr >> 16) & 0o170000 == 0o120000
    except Exception:
        return False

def validate_zip(file):
    try:
        if not file.name.lower().endswith('.zip'):
            raise ValidationError("Only .zip files allowed.")
        if file.size > MAX_ZIP_SIZE:
            raise ValidationError(f"ZIP too large: {file.size//(1024*1024)}MB. Max {MAX_ZIP_SIZE//(1024*1024)}MB.")
        if file.size == 0:
            raise ValidationError("ZIP is empty.")
    except ValidationError:
        raise
    except Exception as e:
        logger.exception(f"validate_zip size check crush: {e}")
        raise ValidationError("Invalid ZIP file.")

    try:
        with zipfile.ZipFile(file) as z:
            bad = z.testzip()
            if bad:
                raise ValidationError(f"Corrupted file in ZIP: {bad}")

            infos = z.infolist()
            if not infos:
                raise ValidationError("ZIP is empty (no files).")
            if len(infos) > MAX_FILES:
                raise ValidationError(
                    f"Too many files ({len(infos)}), max {MAX_FILES}. This is almost always "
                    f"build output — delete node_modules/, .venv/ and dist/ and zip the "
                    f"project again."
                )

            total_uncompressed = 0
            for info in infos:
                name = info.filename

                if not name:
                    raise ValidationError("Empty filename in ZIP.")
                if len(name) > MAX_FILENAME_LEN:
                    raise ValidationError(f"Filename too long: {name[:50]}...")
                norm = name.replace('\\', '/')
                if norm.startswith('/') or norm.startswith('//') or ':/' in norm or norm.startswith('\\\\'):
                    raise ValidationError(f"Absolute path not allowed: {name}")
                try:
                    safe_base = os.path.abspath("/tmp/safe")
                    target = os.path.abspath(os.path.join(safe_base, norm))
                    if os.path.commonpath([safe_base]) != os.path.commonpath([safe_base, target]):
                        raise ValidationError(f"Path traversal detected: {name}")
                except ValidationError:
                    raise
                except Exception:
                    if '..' in norm.split('/'):
                        raise ValidationError(f"Path traversal '..' in: {name}")

                parts = norm.split('/')
                if any(part == '..' for part in parts):
                    raise ValidationError(f"Path traversal '..' in: {name}")
                for part in parts:
                    if part in BULK_NAMES:
                        raise ValidationError(
                            f"“{part}/” is in your ZIP. Delete that folder and zip the "
                            f"project again — it is build output, not your app "
                            f"(it also pushes you over the file limit)."
                        )
                    if part.startswith('.env') and part != '.env.example':
                        raise ValidationError(
                            f"“{name}” is an environment file. Keep the keys out: rename "
                            f"it to .env.example with empty values, and delete the real one."
                        )
                    if part in BLOCKED_NAMES:
                        raise ValidationError(
                            f"“{part}” looks like credentials or tool config: {name}. "
                            f"Remove it and re-upload — real keys belong in your host's "
                            f"environment settings, never in a public vibe."
                        )
                    if part in ('.ssh', '.aws'):
                        raise ValidationError(f"Blocked hidden file: {name}")

                if _is_symlink(info):
                    raise ValidationError(f"Symlink not allowed in ZIP: {name} — would allow /etc/passwd")

                _, ext = os.path.splitext(name)
                if ext.lower() in BLOCKED_EXT:
                    raise ValidationError(f"Blocked file type {ext}: {name}")

                try:
                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                        raise ValidationError(f"Uncompressed total >{MAX_TOTAL_UNCOMPRESSED//(1024*1024)}MB — possible zip bomb. Total {total_uncompressed//(1024*1024)}MB")
                    if info.compress_size > 0:
                        ratio = info.file_size / max(1, info.compress_size)
                        if ratio > MAX_COMPRESSION_RATIO and info.file_size > 1024*1024:
                            raise ValidationError(f"High compression ratio {ratio:.0f}x for {name} ({info.file_size//1024}KB → {info.compress_size//1024}KB) — possible bomb")
                    if info.file_size > 50*1024*1024:
                        raise ValidationError(f"Single file too large {info.file_size//(1024*1024)}MB: {name} — max 50MB per file")
                except ValidationError:
                    raise
                except Exception as e:
                    logger.warning(f"zip bomb check failed for {name}: {e}")

            if total_uncompressed == 0:
                raise ValidationError("ZIP has no content (all directories).")

    except ValidationError:
        raise
    except zipfile.BadZipFile:
        raise ValidationError("Invalid or corrupted ZIP.")
    except Exception as e:
        logger.exception(f"validate_zip crush: {e}")
        raise ValidationError("Invalid ZIP file — failed silently, logged to Sentry.")

def scan_for_secrets_text(text: str):
    try:
        found = []
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                found.append(pat.pattern[:20]+"...")
        return found
    except Exception:
        return []


def normalize_zip_name(name: str) -> str:
    return (name or '').replace('\\', '/')


def zip_name_parts(name: str):
    return [part for part in normalize_zip_name(name).split('/') if part and part != '.']


def is_safe_zip_name(name: str) -> bool:
    """Reject absolute paths, drive letters, `..`, and blocked names."""
    if not name:
        return False
    if len(name) > MAX_FILENAME_LEN:
        return False
    norm = normalize_zip_name(name)
    if norm.startswith('/') or norm.startswith('//') or ':/' in norm:
        return False
    parts = zip_name_parts(norm)
    if not parts or any(part == '..' for part in parts):
        return False
    for part in parts:
        if part in BLOCKED_NAMES:
            return False
        if part.startswith('.env') and part != '.env.example':
            return False
    return True


def assert_safe_zip_info(info):
    """Shared gate for validate_zip and safe_extract_zip."""
    name = info.filename
    if _is_symlink(info):
        raise ValueError(f'symlink not allowed: {name}')
    if not is_safe_zip_name(name):
        raise ValueError(f'unsafe path: {name}')
    if info.file_size > 50 * 1024 * 1024:
        raise ValueError(f'single file too large: {name}')


def _under_dest(dest, path):
    dest_real = os.path.realpath(dest)
    path_real = os.path.realpath(path)
    try:
        return os.path.commonpath([dest_real]) == os.path.commonpath([dest_real, path_real])
    except ValueError:
        return False


def _write_extracted_file(src, target, remaining_budget):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    written = 0
    try:
        with os.fdopen(fd, 'wb') as out:
            fd = None
            while True:
                chunk = src.read(1024 * 256)
                if not chunk:
                    break
                written += len(chunk)
                if written > remaining_budget:
                    raise ValueError('uncompressed total exceeded during extract')
                out.write(chunk)
    finally:
        if fd is not None:
            os.close(fd)
    return written


def safe_extract_zip(zip_path, dest_dir):
    """Extract members one-by-one under dest_dir.

    5 Whys:
    1. Why not extractall? It writes `../` and symlinks before we can stop it.
    2. Why re-check names here? Admin upload, PR merge, and seed skip the form.
    3. Why count bytes while writing? Declared file_size is attacker-controlled.
    4. Why O_NOFOLLOW + realpath? mkdir then replace-parent-with-symlink is the
       classic extract race.
    5. Why refuse blocked names on extract too? A `.env` that got past upload
       must not land on the worker disk next to the scanner.
    """
    dest = os.path.abspath(dest_dir)
    os.makedirs(dest, exist_ok=True)
    written_total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError(f'too many files: {len(infos)}')
        for info in infos:
            if info.is_dir() or info.filename.endswith('/'):
                continue
            assert_safe_zip_info(info)
            rel_parts = zip_name_parts(info.filename)
            target = dest
            for part in rel_parts[:-1]:
                target = os.path.join(target, part)
                if os.path.islink(target):
                    raise ValueError(f'symlink in extract path: {info.filename}')
                if not os.path.isdir(target):
                    os.mkdir(target)
                if not _under_dest(dest, target):
                    raise ValueError(f'path traversal: {info.filename}')
            target = os.path.join(target, rel_parts[-1])
            if os.path.islink(target):
                raise ValueError(f'symlink not allowed: {info.filename}')
            if not _under_dest(dest, os.path.dirname(target)):
                raise ValueError(f'path traversal: {info.filename}')
            remaining = MAX_TOTAL_UNCOMPRESSED - written_total
            with zf.open(info, 'r') as src:
                written_total += _write_extracted_file(src, target, remaining)
            if not _under_dest(dest, target):
                try:
                    os.remove(target)
                except OSError:
                    pass
                raise ValueError(f'path traversal after write: {info.filename}')
    return dest
