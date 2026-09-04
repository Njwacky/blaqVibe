import zipfile, os, collections

EXT_MAP = {
    '.py': 'Python', '.js': 'JavaScript', '.jsx': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TypeScript',
    '.html': 'HTML', '.css': 'CSS', '.scss': 'SCSS', '.vue': 'Vue', '.rb': 'Ruby', '.php': 'PHP',
    '.go': 'Go', '.rs': 'Rust', '.java': 'Java', '.kt': 'Kotlin', '.swift': 'Swift',
    '.json': 'JSON', '.md': 'Markdown', '.sql': 'SQL', '.sh': 'Shell'
}


def _stats_from_infos(infos):
    counts = collections.Counter()
    total = 0
    for info in infos:
        if info.is_dir():
            continue
        _, ext = os.path.splitext(info.filename)
        lang = EXT_MAP.get(ext.lower())
        if lang:
            counts[lang] += info.file_size + 1
            total += info.file_size + 1
    if not total:
        return {}
    sorted_langs = counts.most_common(5)
    result = {}
    for lang, size in sorted_langs:
        result[lang] = round(size / total * 100)
    if result:
        diff = 100 - sum(result.values())
        first = next(iter(result))
        result[first] += diff
    return result


def detect_languages(zip_path):
    """Local-path variant, kept for scripts that have a real path."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            return _stats_from_infos(z.infolist())
    except Exception:
        return {}


def detect_languages_from_field(file_field):
    """Storage-agnostic variant — works on local disk and S3/R2.

    Why a second entry point? The model save path only has a FieldFile;
    calling .path on it dies on remote storage (NotImplementedError).
    """
    try:
        from .ziputil import open_zip
        with open_zip(file_field) as z:
            return _stats_from_infos(z.infolist())
    except Exception:
        return {}
