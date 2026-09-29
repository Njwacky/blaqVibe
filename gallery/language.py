"""Programming-language stats for uploaded ZIPs — an offline GitHub-Linguist
in miniature.

Deep research: docs/specs/BlaqVibes_Detection_Research.md. The rules follow
github/linguist on purpose, because linguist is the bar users compare against
when they say "the language is wrong". What that means concretely:

1.  Per file, detection order is: known FILENAME (Makefile, Dockerfile, …) →
    shebang (for small extensionless scripts) → extension. Linguist adds
    modelines and a naive-Bayesian content classifier as later strategies;
    both need file content we refuse to pull for every file of a browser
    upload, so the extension map below is deliberately generous instead.
2.  Stats are BYTE-weighted, like linguist — not file-count-weighted — so a
    3-line YAML config cannot outweigh 400 lines of Python.
3.  Vendored paths (node_modules/, vendor/, bower_components/, …), generated
    files (*.min.js, *.map, *.d.ts, lockfiles) and documentation paths
    (docs/, README*, CHANGELOG*, …) are excluded, mirroring linguist's
    vendor.yml / generated rules / documentation.yml. Counting them is the
    single most common way a repo's language bar ends up wrong.
4.  Only programming and markup languages count, like linguist's `type:`
    rule (JSON/YAML/TOML/SQL/Markdown are data or prose and excluded) —
    unless they are ALL the archive holds, in which case they are shown
    rather than an empty bar: a degrade, never a lie.

Every helper is best-effort: a broken zip or a weird name must return
usable stats, never raise — this runs on the model save path.
"""
import os
import re
import zipfile
import collections

# --------------------------------------------------------------------------
# The language table. Names follow linguist's languages.yml so the bar reads
# like GitHub's. Grouped for reviewability; merged into one lookup at import.
# --------------------------------------------------------------------------

_LANGUAGES = {
    # Python family
    'Python': ['.py', '.pyw', '.pyi', '.pyx'],
    # Web scripting
    'JavaScript': ['.js', '.mjs', '.cjs', '.jsx'],
    'TypeScript': ['.ts', '.mts', '.cts', '.tsx'],
    'CoffeeScript': ['.coffee'],
    'Dart': ['.dart'],
    'Lua': ['.lua'],
    'PHP': ['.php'],
    'Perl': ['.pl', '.pm'],
    'Ruby': ['.rb', '.ru', '.gemspec', '.rake'],
    # JVM
    'Java': ['.java'],
    'Kotlin': ['.kt', '.kts'],
    'Groovy': ['.groovy', '.gradle'],
    'Scala': ['.scala', '.sc'],
    'Clojure': ['.clj', '.cljc', '.cljs'],
    # .NET
    'C#': ['.cs'],
    'F#': ['.fs', '.fsi', '.fsx'],
    'Visual Basic .NET': ['.vb'],
    # Native
    'C': ['.c'],
    'C++': ['.cpp', '.cc', '.cp', '.cxx', '.hpp', '.hh', '.hxx', '.c++', '.h++'],
    'Objective-C': ['.m'],
    'Objective-C++': ['.mm'],
    'Rust': ['.rs'],
    'Zig': ['.zig'],
    'Nim': ['.nim'],
    'D': ['.d'],
    'Crystal': ['.cr'],
    # Functional / scientific
    'Go': ['.go'],
    'Swift': ['.swift'],
    'Haskell': ['.hs', '.lhs'],
    'Erlang': ['.erl', '.hrl'],
    'Elixir': ['.ex', '.exs'],
    'R': ['.r'],
    'Julia': ['.jl'],
    'Fortran': ['.f', '.for', '.f90'],
    # Scripting / shells
    'Shell': ['.sh', '.bash', '.zsh', '.fish', '.ksh'],
    'PowerShell': ['.ps1', '.psm1', '.psd1'],
    'Batchfile': ['.bat', '.cmd'],
    'Vim Script': ['.vim'],
    'GDScript': ['.gd'],
    'Game Maker Language': ['.gml'],
    'Solidity': ['.sol'],
    'Elm': ['.elm'],
    'Assembly': ['.asm', '.nasm'],
    # Markup & styles (counted by linguist — markup is detectable)
    'HTML': ['.html', '.htm', '.xhtml'],
    'CSS': ['.css'],
    'SCSS': ['.scss'],
    'Sass': ['.sass'],
    'Less': ['.less'],
    'Stylus': ['.styl'],
    'Vue': ['.vue'],
    'Svelte': ['.svelte'],
    'Astro': ['.astro'],
    'Pug': ['.pug'],
    'Haml': ['.haml'],
}

# Data and prose languages (linguist type: data / prose). Excluded from the
# bar unless the archive holds nothing else.
_DATA_LANGUAGES = {
    'JSON': ['.json', '.jsonc', '.geojson'],
    'YAML': ['.yml', '.yaml'],
    'TOML': ['.toml'],
    'XML': ['.xml', '.plist', '.csproj', '.fsproj', '.vbproj', '.resx'],
    'INI': ['.ini', '.cfg', '.conf', '.properties'],
    'Markdown': ['.md', '.markdown'],
    'reStructuredText': ['.rst'],
    'SQL': ['.sql'],
    'SVG': ['.svg'],
    'GraphQL': ['.graphql', '.gql'],
    'Protocol Buffer': ['.proto'],
    'Text': ['.txt'],
}

EXT_MAP = {}
for _name, _exts in _LANGUAGES.items():
    for _e in _exts:
        EXT_MAP[_e] = _name
for _name, _exts in _DATA_LANGUAGES.items():
    for _e in _exts:
        EXT_MAP.setdefault(_e, _name)

DATA_LANG_NAMES = set(_DATA_LANGUAGES)

# Known filenames (linguist's "filename" strategy — checked before extension).
_FILENAME_MAP = {
    'dockerfile': 'Dockerfile',
    'makefile': 'Makefile',
    'gnumakefile': 'Makefile',
    'justfile': 'Makefile',
    'cmakelists.txt': 'CMake',
    'rakefile': 'Ruby',
    'gemfile': 'Ruby',
    'vagrantfile': 'Ruby',
    'podfile': 'Ruby',
    'brewfile': 'Ruby',
    'jenkinsfile': 'Groovy',
    'buildfile': 'Ruby',
}

# Shebang interpreters (linguist's "shebang" strategy). Only consulted for
# extensionless files, because a shebang beats nothing and an extension
# normally beats a shebang.
_SHEBANG_MAP = {
    'python': 'Python', 'python3': 'Python', 'python2': 'Python',
    'node': 'JavaScript', 'deno': 'JavaScript',
    'ruby': 'Ruby', 'macruby': 'Ruby',
    'perl': 'Perl',
    'lua': 'Lua',
    'php': 'PHP',
    'bash': 'Shell', 'sh': 'Shell', 'zsh': 'Shell', 'ksh': 'Shell',
    'fish': 'Shell',
    'pwsh': 'PowerShell', 'powershell': 'PowerShell',
    'rscript': 'R', 'r': 'R',
    'elixir': 'Elixir',
    'groovy': 'Groovy',
    'scala': 'Scala',
    'julia': 'Julia',
    'awk': 'Shell',
    'coffee': 'CoffeeScript',
}
_SHEBANG_RE = re.compile(rb'^#!.*?([A-Za-z0-9_.+-]+)\s*$')

# Vendored paths — a curated subset of linguist's vendor.yml (the entries
# that actually occur in browser-uploaded zips). Matched against the FULL
# path, case-insensitive; a name counts only as a directory (followed by
# "/"), never as a bare file name. Deliberately NOT vendored, same as
# linguist: `bin/` (real projects keep scripts there), `build/` (too
# generic a name).
_VENDOR_RE = re.compile(
    r'(^|/)(node_modules|bower_components|vendor|vendors|third[_-]?party|3rd[_-]?party|'
    r'externals?|deps|_esy|xvba_modules|carthage|pods|'
    r'__pycache__|\.git|\.github|\.vscode|\.idea|\.cache|cache|'
    r'venv|virtualenv|\.venv|site-packages|'
    r'dist|out|target|coverage|\.next|\.nuxt|\.output|\.turbo|\.parcel-cache|'
    r'obj|gradle/wrapper|ace-builds|flow-typed)/'
    r'|(^|/)\.yarn/(releases|plugins|sdks|versions|unplugged)/'
    r'|(^|/)packages/[^/]+\.[0-9][^/]*/'   # NuGet versioned dirs (linguist's rule)
)
# Files that are configuration noise, never "the language of this project".
_VENDOR_FILE_RE = re.compile(
    r'(^|/)(\.gitignore|\.gitattributes|\.gitmodules|\.dockerignore|\.env(\..*)?|'
    r'gradlew(\.bat)?|mvnw(\.cmd)?|proguard(-rules)?\.pro|'
    r'\.ds_store|\.osx|vagrantfile|jenkinsfile|\.sublime-project|\.sublime-workspace)$'
)
# Lockfiles a package manager regenerates — generated data, not source.
_LOCKFILE_RE = re.compile(
    r'(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|'
    r'bun\.lockb?|poetry\.lock|pipfile\.lock|composer\.lock|gemfile\.lock|'
    r'cargo\.lock|go\.sum|go\.work\.sum|flake\.lock|packages\.lock\.json)$'
)
# Generated files — minified bundles, source maps, TS declaration bundles.
_GENERATED_RE = re.compile(
    r'(\.|-)min\.(js|css)$|\.map$|\.d\.ts$'
    r'|\.bundle\.(js|css)$|(^|/)(bundle|chunk|vendor)[.\-][0-9a-f]{6,}\.(js|css)$'
)
# Documentation paths — linguist's documentation.yml subset.
_DOC_RE = re.compile(
    r'(^|/)(docs?|documentation|javadoc|groovydoc|man|samples?|demos?)/'
    r'|(^|/)(readme|changelog|changes|contributing|copying|install|'
    r'license|licence|authors|notice|code_of_conduct|security)(\.|$)'
    r'|(^|/)citations?(\.|$)'
)
_MAX_FILE_BYTES = 1024 * 1024          # per-file cap: a lying header can't skew the bar
_MAX_SHEBANG_READS = 200               # cap content reads (shebang pass)
_MAX_SHEBANG_FILE_SIZE = 256 * 1024    # only read small extensionless files


def _normalized(path):
    return (path or '').replace('\\', '/').lstrip('/')


def _strip_common_root(paths):
    """GitHub-style zips wrap everything in `<repo>-<ref>/`.

    Why strip here? The exclusion rules above (docs/, env/, dist/) are
    written against the REPO root; one level of wrapper must not hide
    `repo-main/docs/` from them. Only stripped when it is a true common
    prefix of every entry — a real top folder name never gets touched.
    """
    if len(paths) < 2:
        return paths
    firsts = {p.split('/', 1)[0] for p in paths}
    if len(firsts) == 1 and all('/' in p for p in paths):
        return [p.split('/', 1)[1] for p in paths]
    return paths


def _is_excluded(path_lower):
    """Vendor / generated / documentation exclusion — the linguist filters."""
    if _VENDOR_RE.search(path_lower) or _VENDOR_FILE_RE.search(path_lower):
        return True
    if _LOCKFILE_RE.search(path_lower) or _GENERATED_RE.search(path_lower):
        return True
    if _DOC_RE.search(path_lower):
        return True
    return False


def _shebang_language(zf, info):
    """Read the first line of a small extensionless file. Best-effort."""
    try:
        if info.file_size > _MAX_SHEBANG_FILE_SIZE:
            return None
        with zf.open(info) as fh:
            head = fh.read(256)
        first = head.split(b'\n', 1)[0].strip()
        if not first.startswith(b'#!'):
            return None
        tokens = first[2:].split()
        if not tokens:
            return None
        interp = tokens[0].decode('ascii', 'ignore').lower()
        # env wrappers: "#!/usr/bin/env python3" — the interpreter is token 2.
        if interp == 'env' or interp.endswith('/env'):
            if len(tokens) > 1:
                interp = tokens[1].decode('ascii', 'ignore').lower()
        base = interp.rsplit('/', 1)[-1]
        base = re.sub(r'[0-9.]+$', '', base) or base  # python3.11 → python
        return _SHEBANG_MAP.get(base)
    except Exception:
        return None


def _stats_from_zip(zf):
    """Byte-weighted language percentages for an open ZipFile."""
    code_counts = collections.Counter()   # programming + markup
    data_counts = collections.Counter()   # data + prose (fallback only)
    entries = []
    for info in zf.infolist():
        try:
            if info.is_dir():
                continue
            path = _normalized(info.filename)
        except Exception:
            continue
        if path:
            entries.append((info, path))
    paths = _strip_common_root([p for _i, p in entries])
    reads = 0
    for (info, _orig), path in zip(entries, paths):
        lower = path.lower()
        base = lower.rsplit('/', 1)[-1]
        if not base or base.startswith('.'):
            continue
        name = _FILENAME_MAP.get(base)
        ext = os.path.splitext(base)[1]
        if name is None and not ext:
            # No extension: try the shebang strategy, capped.
            if reads < _MAX_SHEBANG_READS:
                reads += 1
                name = _shebang_language(zf, info)
        if name is None and ext:
            name = EXT_MAP.get(ext)
        if not name or _is_excluded(lower):
            continue
        size = min(max(int(getattr(info, 'file_size', 0) or 0), 1), _MAX_FILE_BYTES)
        if name in DATA_LANG_NAMES:
            data_counts[name] += size
        else:
            code_counts[name] += size

    counts = code_counts if code_counts else data_counts
    total = sum(counts.values())
    if not total:
        return {}
    result = {}
    for lang, size in counts.most_common(5):
        result[lang] = round(size / total * 100)
    if result:
        # Keep the bar honest: force the total to exactly 100.
        diff = 100 - sum(result.values())
        first = next(iter(result))
        result[first] += diff
    return result


def detect_languages(zip_path):
    """Local-path variant, kept for scripts that have a real path."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            return _stats_from_zip(z)
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
            return _stats_from_zip(z)
    except Exception:
        return {}
