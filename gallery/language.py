import zipfile, os, collections

# 5 Whys: Why size-weighted not count? 10-line CSS vs 500-line Python should not be equal.
EXT_MAP = {
    '.py': 'Python', '.js': 'JavaScript', '.jsx': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TypeScript',
    '.html': 'HTML', '.css': 'CSS', '.scss': 'SCSS', '.vue': 'Vue', '.rb': 'Ruby', '.php': 'PHP',
    '.go': 'Go', '.rs': 'Rust', '.java': 'Java', '.kt': 'Kotlin', '.swift': 'Swift',
    '.json': 'JSON', '.md': 'Markdown', '.sql': 'SQL', '.sh': 'Shell'
}

def detect_languages(zip_path):
    counts = collections.Counter()
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                if info.is_dir(): continue
                _, ext = os.path.splitext(info.filename)
                lang = EXT_MAP.get(ext.lower())
                if lang:
                    # Weight by file size (bytes) + 1 to avoid 0
                    counts[lang] += info.file_size + 1
                    total += info.file_size + 1
    except: return {}
    if not total:
        return {}
    # Top 5 + Other
    sorted_langs = counts.most_common(5)
    result = {}
    for lang, size in sorted_langs:
        result[lang] = round(size/total*100)
    # Fix rounding to 100
    if result:
        diff = 100 - sum(result.values())
        first = next(iter(result))
        result[first] += diff
    return result
