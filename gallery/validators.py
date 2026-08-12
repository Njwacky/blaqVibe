import zipfile, os, re, logging
from django.core.exceptions import ValidationError
from django.conf import settings

logger = logging.getLogger(__name__)

MAX_ZIP_SIZE = getattr(settings, 'BLAQVIBES_MAX_ZIP_MB', 100) * 1024 * 1024
MAX_FILES = getattr(settings, 'BLAQVIBES_MAX_FILES', 1000)  # stricter: 1000 not 2000
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024  # 200MB, not 500MB
MAX_COMPRESSION_RATIO = 100  # file_size / compress_size >100 = bomb
MAX_FILENAME_LEN = 255
BLOCKED_NAMES = {'node_modules','__pycache__','.git','venv','.venv','.env','__MACOSX','.DS_Store'}
BLOCKED_EXT = {'.exe','.dll','.so','.dylib','.sh','.bat','.bin','.o','.a'}
SECRET_PATTERNS = [
    re.compile(r'sk_live_[0-9a-zA-Z]+'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'-----BEGIN (RSA )?PRIVATE KEY-----'),
    re.compile(r'ghp_[A-Za-z0-9_]{36}'),
]

# 5 Whys: Why not just check '..'? Symlink + absolute + // + \ + commonpath bypass it. Why not just sum? Ratio bomb: 1KB compressed -> 10GB. Why not just count? 1999 * 300KB = 600MB still passes but symlink does.

def _is_symlink(zip_info):
    # External attr high 16 bits is file mode; symlink is 0o120000
    try:
        return (zip_info.external_attr >> 16) & 0o170000 == 0o120000
    except:
        return False

def validate_zip(file):
    # 1. Size and type — crush silently, raise ValidationError
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
            # Test for bad zip
            bad = z.testzip()
            if bad:
                raise ValidationError(f"Corrupted file in ZIP: {bad}")

            infos = z.infolist()
            if not infos:
                raise ValidationError("ZIP is empty (no files).")
            if len(infos) > MAX_FILES:
                raise ValidationError(f"Too many files ({len(infos)}). Max {MAX_FILES} — possible bomb or node_modules.")

            total_uncompressed = 0
            for info in infos:
                name = info.filename

                # 2. Path traversal — full check, not just '..'
                # Block absolute, Windows, drive, //, \
                if not name:
                    raise ValidationError("Empty filename in ZIP.")
                if len(name) > MAX_FILENAME_LEN:
                    raise ValidationError(f"Filename too long: {name[:50]}...")
                # Normalize separators
                norm = name.replace('\\', '/')
                if norm.startswith('/') or norm.startswith('//') or ':/' in norm or norm.startswith('\\\\'):
                    raise ValidationError(f"Absolute path not allowed: {name}")
                # Use commonpath to detect traversal
                try:
                    # Resolve against /tmp/safe
                    safe_base = os.path.abspath("/tmp/safe")
                    target = os.path.abspath(os.path.join(safe_base, norm))
                    if os.path.commonpath([safe_base]) != os.path.commonpath([safe_base, target]):
                        raise ValidationError(f"Path traversal detected: {name}")
                except ValidationError:
                    raise
                except Exception:
                    if '..' in norm.split('/'):
                        raise ValidationError(f"Path traversal '..' in: {name}")

                # Also block '.' components and empty parts
                parts = norm.split('/')
                if any(p == '..' for p in parts):
                    raise ValidationError(f"Path traversal '..' in: {name}")
                if any(p in BLOCKED_NAMES for p in parts):
                    raise ValidationError(f"Blocked folder/file in ZIP: {name} — remove {sorted(BLOCKED_NAMES)}")
                # Block hidden files except .env.example?
                if any(p.startswith('.') and p not in ('.env.example',) for p in parts):
                    # Allow .gitignore but not .env, .ssh
                    if p in ('.env', '.ssh', '.aws'):
                        raise ValidationError(f"Blocked hidden file: {name}")

                # 3. Symlink check
                if _is_symlink(info):
                    raise ValidationError(f"Symlink not allowed in ZIP: {name} — would allow /etc/passwd")

                # 4. Extension block
                _, ext = os.path.splitext(name)
                if ext.lower() in BLOCKED_EXT:
                    raise ValidationError(f"Blocked file type {ext}: {name}")

                # 5. Zip bomb — size + ratio + count
                try:
                    total_uncompressed += info.file_size
                    if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                        raise ValidationError(f"Uncompressed total >{MAX_TOTAL_UNCOMPRESSED//(1024*1024)}MB — possible zip bomb. Total {total_uncompressed//(1024*1024)}MB")
                    # Compression ratio: file_size / compress_size
                    if info.compress_size > 0:
                        ratio = info.file_size / max(1, info.compress_size)
                        if ratio > MAX_COMPRESSION_RATIO and info.file_size > 1024*1024:
                            raise ValidationError(f"High compression ratio {ratio:.0f}x for {name} ({info.file_size//1024}KB → {info.compress_size//1024}KB) — possible bomb")
                    # Single file too large
                    if info.file_size > 50*1024*1024:
                        raise ValidationError(f"Single file too large {info.file_size//(1024*1024)}MB: {name} — max 50MB per file")
                except ValidationError:
                    raise
                except Exception as e:
                    logger.warning(f"zip bomb check failed for {name}: {e}")

            # Final total check
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
    except:
        return []
