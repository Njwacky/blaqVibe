"""Static-site live runner — turn a ZIP of HTML/CSS/JS into one document
that runs inside the *existing* sandboxed, opaque-origin preview iframe.

Public entry points:
  * `detect_static_runnable(paths)` — (runnable, entry) from a file list.
  * `assemble_runnable_document(zip_field, entry)` — one self-contained
    HTML string with local CSS/JS/images inlined.

5 Whys — why assemble ONE document instead of serving each file?

1. Why not serve `/run/<path>` per file? The whole preview safety model is
   an opaque-origin sandbox (`sandbox allow-scripts`, no allow-same-origin).
   In an opaque origin `'self'` matches nothing, so an external
   `<script src="app.js">` served from our host is either blocked by CSP or
   forces `allow-same-origin` — which hands user JS our cookies. Inlining
   keeps the proven single-document model with zero new origin surface.
2. Why is that safe when the files are attacker-controlled? Identical to
   `snippet_doc`: the bytes only ever reach a browser inside an
   `<iframe sandbox="allow-scripts">` whose response also carries the CSP
   `sandbox` directive, so even opened directly it is an opaque origin that
   cannot read cookies, localStorage, or the parent DOM.
3. Why only *static* ZIPs? A React/Django source tree does not run from raw
   files — rendering `index.html` for it would be the fake preview the site
   refuses to show. `detect_static_runnable` says "runnable" only when a
   real HTML document is the entry and the archive is mostly client-side
   assets, so the badge stays honest.
4. Why inline images as data URIs but leave `https://` refs alone? A local
   `logo.png` cannot load in an opaque origin (no `'self'`), so it must be
   embedded; a CDN URL already loads under the snippet CSP's `img-src`.
   Anything we cannot resolve is left untouched — it simply may not load,
   which is honest, not a crash.
5. Why cap sizes and count? A 100 MB ZIP of images inlined as base64 would
   be a multi-hundred-MB response built in a request. Budgets keep the
   assembled document bounded; assets over budget are dropped, not streamed.
"""
import base64
import logging
import os
import posixpath
import re

logger = logging.getLogger(__name__)

# --- Budgets (why: an assembled document is built in-request; keep it bounded).
MAX_ENTRY_BYTES = 512 * 1024          # a hand-written index.html over 512 KB is not a vibe
MAX_INLINE_TEXT_BYTES = 512 * 1024    # a single css/js file
MAX_INLINE_IMAGE_BYTES = 512 * 1024   # a single image → data URI
MAX_TOTAL_ASSEMBLED = 6 * 1024 * 1024 # whole assembled document
MAX_INLINE_ASSETS = 60                # stop after N resolved assets

# Extensions that make an archive "client-side" (why: presence of these and
# absence of a build step is what "runs in a browser" means).
_STATIC_EXT = {
    '.html', '.htm', '.css', '.js', '.mjs', '.json', '.svg', '.png', '.jpg',
    '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.avif', '.woff', '.woff2',
    '.ttf', '.otf', '.eot', '.map', '.txt', '.md', '.xml', '.csv', '.wasm',
    '.mp3', '.wav', '.ogg', '.mp4', '.webm',
}
# Files that mean "this needs a build or a server before it runs" (why: raw
# JSX/TS/Vue/py/php do not execute from a ZIP; claiming a live run would lie).
_BUILD_MARKERS = {
    'package.json', 'vite.config.js', 'vite.config.ts', 'webpack.config.js',
    'next.config.js', 'svelte.config.js', 'angular.json', 'requirements.txt',
    'manage.py', 'composer.json', 'gemfile', 'cargo.toml', 'go.mod', 'pom.xml',
}
_BUILD_EXT = {'.jsx', '.tsx', '.ts', '.vue', '.svelte', '.py', '.php', '.rb', '.go', '.rs', '.java'}

_IMAGE_MIME = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon', '.bmp': 'image/bmp', '.avif': 'image/avif',
}


def _ext(name: str) -> str:
    return os.path.splitext((name or '').lower())[1]


def detect_static_runnable(paths):
    """Return (runnable: bool, entry: str) for a list of archive paths.

    5 Whys:
    1. Why prefer a root `index.html`? It is the universal "open me first"
       for a static site; a browser opening a folder does the same.
    2. Why fall back to the shallowest .html? A vibe may ship `public/index.html`
       or `home.html`; the shallowest full HTML document is the honest entry.
    3. Why refuse when a build marker is present? `package.json` + `src/App.jsx`
       is source, not a runnable site — the raw files render blank or broken,
       which is the fake preview the site forbids.
    4. Why require the archive be *mostly* static? One stray `.py` beside a
       finished static export is fine; a tree that is 80% server code is an
       app that needs hosting, not a browser preview.
    5. Why return the entry, not just a bool? The assembler and the runner
       view both need to know which document to open; deriving it twice risks
       them disagreeing.
    """
    try:
        files = [p.replace('\\', '/').lstrip('/') for p in (paths or []) if p and not p.endswith('/')]
        if not files:
            return False, ''
        # A build marker anywhere means "needs build/host", not runnable.
        lower = {f.lower() for f in files}
        base_lower = {posixpath.basename(f) for f in lower}
        if base_lower & _BUILD_MARKERS:
            return False, ''

        html_files = [f for f in files if _ext(f) in ('.html', '.htm')]
        if not html_files:
            return False, ''

        # Mostly-static check: count files that would need a build/server.
        build_like = sum(1 for f in files if _ext(f) in _BUILD_EXT)
        if build_like and build_like * 2 >= len(files):
            return False, ''

        # Pick the entry: root index.html → any index.html → shallowest html.
        def depth(f):
            return f.count('/')

        root_index = [f for f in html_files if f.lower() == 'index.html']
        if root_index:
            return True, root_index[0]
        any_index = sorted(
            [f for f in html_files if posixpath.basename(f).lower() == 'index.html'],
            key=depth,
        )
        if any_index:
            return True, any_index[0]
        shallowest = sorted(html_files, key=lambda f: (depth(f), len(f)))
        return True, shallowest[0]
    except Exception:
        logger.exception('detect_static_runnable failed')
        return False, ''


def _read_member(zf, name):
    """Read a ZIP member by name, tolerant of leading './'. Returns bytes|None."""
    try:
        names = set(zf.namelist())
        for candidate in (name, './' + name, name.lstrip('./')):
            if candidate in names:
                return zf.read(candidate)
    except Exception:
        pass
    return None


def _resolve(base_dir, ref):
    """Resolve a relative asset ref against the entry's directory.

    Why normalise here? `../img/x.png` and `./style.css` must map to real
    archive members; a raw string lookup would miss both. Absolute and remote
    refs return None so the caller leaves them untouched.
    """
    ref = (ref or '').strip()
    if not ref:
        return None
    low = ref.lower()
    # Leave remote / data / anchor / protocol-relative refs alone.
    if low.startswith(('http://', 'https://', 'data:', 'mailto:', 'tel:', '//', '#', 'javascript:')):
        return None
    ref = ref.split('#', 1)[0].split('?', 1)[0]
    if not ref:
        return None
    if ref.startswith('/'):
        joined = ref.lstrip('/')
    else:
        joined = posixpath.normpath(posixpath.join(base_dir, ref))
    if joined.startswith('..') or joined.startswith('/'):
        return None
    return joined


def assemble_runnable_document(zip_field, entry):
    """Build ONE self-contained HTML string for the entry document.

    Local `<link rel=stylesheet>` → `<style>`, local `<script src>` →
    inline `<script>`, local `<img src>`/`href` icons → data URIs. Remote
    and unresolvable refs are left as written. Returns '' on any failure
    (caller shows the honest "no live preview" state — never a crash).
    """
    from .ziputil import open_zip
    try:
        with open_zip(zip_field) as zf:
            raw = _read_member(zf, entry)
            if raw is None or len(raw) > MAX_ENTRY_BYTES:
                return ''
            try:
                html = raw.decode('utf-8')
            except UnicodeDecodeError:
                html = raw.decode('latin-1', errors='replace')
            base_dir = posixpath.dirname(entry)
            budget = {'total': len(html), 'assets': 0}

            html = _inline_stylesheets(html, zf, base_dir, budget)
            html = _inline_scripts(html, zf, base_dir, budget)
            html = _inline_images(html, zf, base_dir, budget)
            return html
    except Exception:
        logger.exception('assemble_runnable_document failed for entry=%s', entry)
        return ''


def _over_budget(budget, extra):
    if budget['assets'] >= MAX_INLINE_ASSETS:
        return True
    if budget['total'] + extra > MAX_TOTAL_ASSEMBLED:
        return True
    return False


def _inline_stylesheets(html, zf, base_dir, budget):
    def repl(m):
        tag = m.group(0)
        href = m.group('url')
        target = _resolve(base_dir, href)
        if not target:
            return tag
        data = _read_member(zf, target)
        if data is None or len(data) > MAX_INLINE_TEXT_BYTES:
            return tag
        try:
            css = data.decode('utf-8')
        except UnicodeDecodeError:
            return tag
        if _over_budget(budget, len(css)):
            return tag
        budget['total'] += len(css)
        budget['assets'] += 1
        # Neutralise a stray closing tag so the CSS cannot break out of <style>.
        css = css.replace('</style', '<\\/style')
        return f'<style data-inlined-from="{href}">\n{css}\n</style>'

    # <link ... rel="stylesheet" ... href="...">  (attr order-independent)
    pattern = re.compile(
        r'<link\b(?=[^>]*\brel\s*=\s*["\']?stylesheet["\']?)[^>]*?\bhref\s*=\s*["\'](?P<url>[^"\']+)["\'][^>]*>',
        re.IGNORECASE,
    )
    return pattern.sub(repl, html)


def _inline_scripts(html, zf, base_dir, budget):
    def repl(m):
        tag = m.group(0)
        src = m.group('url')
        target = _resolve(base_dir, src)
        if not target:
            return tag
        data = _read_member(zf, target)
        if data is None or len(data) > MAX_INLINE_TEXT_BYTES:
            return tag
        try:
            js = data.decode('utf-8')
        except UnicodeDecodeError:
            return tag
        if _over_budget(budget, len(js)):
            return tag
        budget['total'] += len(js)
        budget['assets'] += 1
        # Preserve type="module" so ES-module scripts still behave.
        is_module = bool(re.search(r'type\s*=\s*["\']?module["\']?', tag, re.IGNORECASE))
        type_attr = ' type="module"' if is_module else ''
        js = js.replace('</script', '<\\/script')
        return f'<script{type_attr} data-inlined-from="{src}">\n{js}\n</script>'

    pattern = re.compile(
        r'<script\b[^>]*?\bsrc\s*=\s*["\'](?P<url>[^"\']+)["\'][^>]*>\s*</script>',
        re.IGNORECASE,
    )
    return pattern.sub(repl, html)


def _inline_images(html, zf, base_dir, budget):
    def repl(m):
        prefix, quote, url, suffix = m.group('pre'), m.group('q'), m.group('url'), m.group('post')
        target = _resolve(base_dir, url)
        if not target:
            return m.group(0)
        ext = _ext(target)
        mime = _IMAGE_MIME.get(ext)
        if not mime:
            return m.group(0)
        data = _read_member(zf, target)
        if data is None or len(data) > MAX_INLINE_IMAGE_BYTES:
            return m.group(0)
        if _over_budget(budget, len(data) * 4 // 3):
            return m.group(0)
        budget['total'] += len(data) * 4 // 3
        budget['assets'] += 1
        b64 = base64.b64encode(data).decode('ascii')
        return f'{prefix}{quote}data:{mime};base64,{b64}{quote}{suffix}'

    # src="..." and href="..." for icons; keep the surrounding attribute intact.
    pattern = re.compile(
        r'(?P<pre>\b(?:src|href)\s*=\s*)(?P<q>["\'])(?P<url>[^"\']+)(?P=q)(?P<post>)',
        re.IGNORECASE,
    )
    return pattern.sub(repl, html)
