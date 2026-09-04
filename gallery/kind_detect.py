"""Heuristic program-kind detection — the deterministic floor.
`classify.classify_project()` is the public entry point; this module is the
part that must work with **no API key, no network, and no money**.
"""
import re

from .taxonomy import DEFAULT_KIND, KIND_VALUES, coerce_kind

# Signal tables. Weight scale is deliberately coarse:
#   5 = near-proof (a Unity ProjectSettings folder, an .apk)
#   3 = strong (manifest.json, requirements + Dockerfile)
#   2 = supporting (tech_stack mentions "phaser")
#   1 = weak (the word "game" in the README)

# Exact file/dir names, matched on the basename of shallow paths.
_NAME_SIGNALS = {
    # game
    'projectsettings': [('game', 5)],
    'projectversion.txt': [('game', 5)],
    'project.godot': [('game', 5)],
    'export_presets.cfg': [('game', 4)],
    'love.js': [('game', 3)],
    'game.js': [('game', 3)],
    'gamemanager.cs': [('game', 4)],
    'pubspec.yaml': [('mobile_app', 4)],
    # mobile
    'androidmanifest.xml': [('mobile_app', 5)],
    'build.gradle': [('mobile_app', 4)],
    'build.gradle.kts': [('mobile_app', 4)],
    'info.plist': [('mobile_app', 3)],
    'podfile': [('mobile_app', 4)],
    'app.json': [('mobile_app', 2)],
    'capacitor.config.json': [('mobile_app', 4)],
    'capacitor.config.ts': [('mobile_app', 4)],
    # desktop
    'main.qml': [('desktop_app', 4)],
    'tauri.conf.json': [('desktop_app', 5)],
    'electron.js': [('desktop_app', 4)],
    'electron-builder.yml': [('desktop_app', 5)],
    'forge.config.js': [('desktop_app', 4)],
    # extension
    'manifest.json': [('extension', 2)],
    'content_script.js': [('extension', 4)],
    'background.js': [('extension', 2)],
    # api / backend
    'manage.py': [('api_backend', 4)],
    'wsgi.py': [('api_backend', 4)],
    'asgi.py': [('api_backend', 4)],
    'settings.py': [('api_backend', 2)],
    'urls.py': [('api_backend', 2)],
    'server.js': [('api_backend', 3)],
    'app.py': [('api_backend', 2)],
    'main.go': [('api_backend', 2)],
    'dockerfile': [('api_backend', 2)],
    'docker-compose.yml': [('api_backend', 2)],
    'procfile': [('api_backend', 2)],
    'artisan': [('api_backend', 4)],
    'nest-cli.json': [('api_backend', 4)],
    # ai / ml
    'train.py': [('ai_ml', 4)],
    'model.py': [('ai_ml', 2)],
    'inference.py': [('ai_ml', 4)],
    'requirements-gpu.txt': [('ai_ml', 3)],
    # data
    'dashboard.py': [('data_viz', 3)],
    'streamlit_app.py': [('data_viz', 4)],
    # cli
    'cli.py': [('cli_tool', 3)],
    '__main__.py': [('cli_tool', 2)],
    # library
    'setup.py': [('library', 3)],
    'pyproject.toml': [('library', 1)],
    'cargo.toml': [('library', 1)],
    'rollup.config.js': [('library', 2)],
    # static / web
    'index.html': [('static_site', 2)],
    'next.config.js': [('web_app', 3)],
    'next.config.mjs': [('web_app', 3)],
    'vite.config.js': [('web_app', 2)],
    'vite.config.ts': [('web_app', 2)],
    'nuxt.config.ts': [('web_app', 3)],
    'angular.json': [('web_app', 4)],
    'svelte.config.js': [('web_app', 3)],
    'tailwind.config.js': [('static_site', 1)],
    'package.json': [('web_app', 1)],
}

# Directory names anywhere in a shallow path.
_DIR_SIGNALS = {
    'assets': [('game', 1)],
    'sprites': [('game', 4)],
    'levels': [('game', 3)],
    'scenes': [('game', 2)],
    'shaders': [('game', 3)],
    'notebooks': [('ai_ml', 3), ('data_viz', 2)],
    'models': [('ai_ml', 2)],
    'datasets': [('ai_ml', 2), ('data_viz', 3)],
    'migrations': [('api_backend', 2)],
    'templates': [('api_backend', 1)],
    'components': [('web_app', 1)],
    'pages': [('web_app', 1)],
    'ios': [('mobile_app', 3)],
    'android': [('mobile_app', 3)],
}

# File extensions.
_EXT_SIGNALS = {
    '.unity': [('game', 5)],
    '.prefab': [('game', 5)],
    '.tscn': [('game', 5)],
    '.gd': [('game', 5)],
    '.blend': [('game', 3)],
    '.apk': [('mobile_app', 5)],
    '.aab': [('mobile_app', 5)],
    '.ipa': [('mobile_app', 5)],
    '.xcodeproj': [('mobile_app', 5)],
    '.dart': [('mobile_app', 4)],
    '.swift': [('mobile_app', 3)],
    '.kt': [('mobile_app', 2)],
    '.ipynb': [('ai_ml', 4), ('data_viz', 2)],
    '.pkl': [('ai_ml', 3)],
    '.h5': [('ai_ml', 3)],
    '.onnx': [('ai_ml', 4)],
    '.pt': [('ai_ml', 3)],
    '.csv': [('data_viz', 2)],
    '.parquet': [('data_viz', 3)],
    '.ino': [('other', 3)],
    '.vue': [('web_app', 2)],
    '.jsx': [('web_app', 2)],
    '.tsx': [('web_app', 2)],
    '.html': [('static_site', 1)],
}

# Words in title / description / tech stack / README. Word-boundary matched.
_TEXT_SIGNALS = {
    'game': [('game', 3)],
    'games': [('game', 3)],
    'gameplay': [('game', 4)],
    'player': [('game', 2)],
    'arcade': [('game', 4)],
    'platformer': [('game', 5)],
    'roguelike': [('game', 5)],
    'puzzle': [('game', 2)],
    'shooter': [('game', 4)],
    'rpg': [('game', 4)],
    'multiplayer': [('game', 3)],
    'leaderboard': [('game', 1)],
    'unity': [('game', 4)],
    'godot': [('game', 5)],
    'phaser': [('game', 5)],
    'pygame': [('game', 5)],
    'threejs': [('game', 2)],
    'kaboom': [('game', 4)],
    'love2d': [('game', 5)],
    'unreal': [('game', 4)],
    'dashboard': [('data_viz', 3)],
    'analytics': [('data_viz', 3)],
    'chart': [('data_viz', 2)],
    'charts': [('data_viz', 2)],
    'report': [('data_viz', 1)],
    'pandas': [('data_viz', 2), ('ai_ml', 1)],
    'notebook': [('ai_ml', 2), ('data_viz', 2)],
    'jupyter': [('ai_ml', 3), ('data_viz', 1)],
    'tensorflow': [('ai_ml', 5)],
    'pytorch': [('ai_ml', 5)],
    'sklearn': [('ai_ml', 4)],
    'llm': [('ai_ml', 4)],
    'gpt': [('ai_ml', 3)],
    'openai': [('ai_ml', 3)],
    'langchain': [('ai_ml', 5)],
    'rag': [('ai_ml', 3)],
    'agent': [('ai_ml', 2)],
    'classifier': [('ai_ml', 4)],
    'chatbot': [('bot', 4), ('ai_ml', 1)],
    'telegram': [('bot', 4)],
    'discord': [('bot', 4)],
    'whatsapp': [('bot', 3)],
    'scraper': [('bot', 4)],
    'scraping': [('bot', 3)],
    'automation': [('bot', 3)],
    'cron': [('bot', 2)],
    'api': [('api_backend', 2)],
    'rest': [('api_backend', 2)],
    'graphql': [('api_backend', 3)],
    'backend': [('api_backend', 3)],
    'microservice': [('api_backend', 4)],
    'django': [('api_backend', 2)],
    'flask': [('api_backend', 3)],
    'fastapi': [('api_backend', 4)],
    'express': [('api_backend', 2)],
    'laravel': [('api_backend', 3)],
    'android': [('mobile_app', 3)],
    'ios': [('mobile_app', 3)],
    'flutter': [('mobile_app', 5)],
    'react native': [('mobile_app', 5)],
    'expo': [('mobile_app', 4)],
    'swiftui': [('mobile_app', 5)],
    'mobile app': [('mobile_app', 4)],
    'electron': [('desktop_app', 5)],
    'tauri': [('desktop_app', 5)],
    'desktop': [('desktop_app', 3)],
    'tkinter': [('desktop_app', 4)],
    'pyqt': [('desktop_app', 4)],
    'extension': [('extension', 3)],
    'chrome extension': [('extension', 5)],
    'firefox add-on': [('extension', 5)],
    'cli': [('cli_tool', 3)],
    'command line': [('cli_tool', 4)],
    'command-line': [('cli_tool', 4)],
    'terminal': [('cli_tool', 2)],
    'script': [('cli_tool', 1)],
    'library': [('library', 3)],
    'package': [('library', 1)],
    'sdk': [('library', 4)],
    'npm package': [('library', 4)],
    'template': [('template', 3)],
    'boilerplate': [('template', 4)],
    'starter': [('template', 3)],
    'ui kit': [('template', 4)],
    'theme': [('template', 2)],
    'portfolio': [('static_site', 3)],
    'landing page': [('static_site', 4)],
    'landing': [('static_site', 2)],
    'blog': [('static_site', 2)],
    'website': [('static_site', 2)],
    'web app': [('web_app', 3)],
    'react': [('web_app', 2)],
    'vue': [('web_app', 2)],
    'svelte': [('web_app', 2)],
    'next.js': [('web_app', 3)],
    'nextjs': [('web_app', 3)],
    'crud': [('web_app', 2)],
    'saas': [('web_app', 2)],
}

_LANGUAGE_SIGNALS = {
    'Python': [('api_backend', 1), ('cli_tool', 1)],
    'JavaScript': [('web_app', 1)],
    'TypeScript': [('web_app', 1)],
    'HTML': [('static_site', 1)],
    'CSS': [('static_site', 1)],
    'Vue': [('web_app', 2)],
    'Swift': [('mobile_app', 2)],
    'Kotlin': [('mobile_app', 2)],
    'Java': [('mobile_app', 1)],
    'Go': [('api_backend', 1)],
    'Rust': [('cli_tool', 1)],
    'PHP': [('api_backend', 1)],
    'Ruby': [('api_backend', 1)],
}

# Max evidence a single source may contribute, so a README that repeats
# "game" forty times cannot outvote the actual file tree.
_TEXT_CAP_PER_TERM = 1
_MAX_PATHS = 400

def _shallow_paths(project):
    """Lower-cased shallow file paths (root + one folder deep).

    Why shallow? Same reason as artifact_detect: `node_modules/x/package.json`
    is a dependency, not a statement about the project. Why capped at 400?
    A 1000-file ZIP must not turn classification into an O(files) scan on
    the publish path.
    """
    paths = []
    try:
        files = getattr(project, '_kind_paths_cache', None)
        if files is not None:
            return files
    except Exception:
        pass
    try:
        for f in project.files.all()[:_MAX_PATHS]:
            p = (f.path or '').replace('\\', '/').strip('/').lower()
            if p:
                paths.append(p)
    except Exception:
        pass
    if not paths and isinstance(getattr(project, 'file_tree', None), dict):
        def walk(node, prefix, depth):
            if depth > 2 or len(paths) >= _MAX_PATHS:
                return
            for name, child in node.items():
                lname = str(name).lower()
                full = f'{prefix}{lname}'
                paths.append(full)
                if isinstance(child, dict):
                    walk(child, f'{full}/', depth + 1)
        try:
            walk(project.file_tree, '', 0)
        except Exception:
            pass
    return paths

def _text_blob(project):
    parts = [
        getattr(project, 'title', '') or '',
        getattr(project, 'short_description', '') or '',
        getattr(project, 'tech_stack', '') or '',
        (getattr(project, 'readme', '') or '')[:4000],
    ]
    return ' '.join(parts).lower()

def _add(scores, evidence, kind, weight, why):
    kind = coerce_kind(kind)
    scores[kind] = scores.get(kind, 0) + weight
    evidence.append((kind, weight, why))

def score_kinds(project):
    """Return (scores dict, evidence list). Pure, no DB writes."""
    scores = {}
    evidence = []

    paths = _shallow_paths(project)
    seen_names = set()
    for path in paths:
        segments = path.split('/')
        base = segments[-1]
        depth = len(segments) - 1
        # Depth discount: root-level evidence is worth more than nested.
        factor = 1.0 if depth == 0 else 0.6
        if base not in seen_names:
            seen_names.add(base)
            for kind, weight in _NAME_SIGNALS.get(base, ()):
                _add(scores, evidence, kind, weight * factor, f'file {base}')
        for seg in segments[:-1]:
            key = ('dir', seg)
            if key in seen_names:
                continue
            seen_names.add(key)
            for kind, weight in _DIR_SIGNALS.get(seg, ()):
                _add(scores, evidence, kind, weight * factor, f'folder {seg}/')
        dot = base.rfind('.')
        if dot > 0:
            ext = base[dot:]
            key = ('ext', ext)
            if key not in seen_names:
                seen_names.add(key)
                for kind, weight in _EXT_SIGNALS.get(ext, ()):
                    _add(scores, evidence, kind, weight * factor, f'{ext} files')

    blob = _text_blob(project)
    for term, hits in _TEXT_SIGNALS.items():
        if ' ' in term or '.' in term or '-' in term:
            found = term in blob
        else:
            found = re.search(rf'\b{re.escape(term)}\b', blob) is not None
        if found:
            for kind, weight in hits:
                _add(scores, evidence, kind, weight * _TEXT_CAP_PER_TERM, f'“{term}”')

    langs = getattr(project, 'language_stats', None) or {}
    if isinstance(langs, dict):
        for lang, pct in langs.items():
            try:
                share = float(pct) / 100.0
            except Exception:
                share = 0.0
            for kind, weight in _LANGUAGE_SIGNALS.get(lang, ()):
                if share >= 0.15:
                    _add(scores, evidence, kind, weight * share * 2, f'{lang} {int(share*100)}%')

    # Shape corrections — cheap facts that override keyword noise.
    has_html = bool((getattr(project, 'html_code', '') or '').strip())
    has_zip = bool(getattr(project, 'zip_file', None))
    if has_html and not has_zip:
        # A pasted HTML/CSS/JS snippet cannot BE a backend, a mobile app or
        # a desktop program — whatever its README talks about.
        from .taxonomy import KIND_BY_VALUE
        for kind in list(scores):
            if not KIND_BY_VALUE.get(kind, {}).get('web_native', False):
                scores[kind] *= 0.25
                evidence.append((kind, 0, 'browser snippet, not a shippable backend'))
        # A pasted snippet is by construction something the browser runs.
        _add(scores, evidence, 'web_app', 2, 'HTML snippet')
        if re.search(r'\b(canvas|requestanimationframe|keydown|score)\b', (project.html_code or '').lower()
                     + ' ' + (getattr(project, 'js_code', '') or '').lower()):
            _add(scores, evidence, 'game', 3, 'canvas/game loop in snippet')
        if not re.search(r'<(button|input|form|canvas)\b', (project.html_code or '').lower()):
            _add(scores, evidence, 'static_site', 2, 'no interactive elements')

    return scores, evidence

def detect_kind(project):
    """Heuristic classification.

    Returns dict: {kind, confidence (0..1), evidence [str], source}.
    Never raises — a classification failure must not block a publish.
    """
    try:
        scores, evidence = score_kinds(project)
        if not scores:
            return {'kind': DEFAULT_KIND, 'confidence': 0.0, 'evidence': [], 'source': 'heuristic'}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_kind, top_score = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        # Confidence blends absolute evidence with the margin over #2: a lone
        # weak signal isn't confident just because nothing competes with it, and
        # a strong signal that ties another kind is genuinely ambiguous.
        strength = min(1.0, top_score / 8.0)
        margin = (top_score - runner_up) / top_score if top_score else 0.0
        confidence = round(max(0.0, min(1.0, 0.55 * strength + 0.45 * margin)), 3)
        top_evidence = [
            why for kind, _w, why in
            sorted((e for e in evidence if e[0] == top_kind), key=lambda e: -e[1])
        ]
        # de-dupe, keep order
        seen, ordered = set(), []
        for why in top_evidence:
            if why not in seen:
                seen.add(why)
                ordered.append(why)
        return {
            'kind': top_kind,
            'confidence': confidence,
            'evidence': ordered[:5],
            'source': 'heuristic',
            'runner_up': ranked[1][0] if len(ranked) > 1 else '',
        }
    except Exception:
        import logging
        logging.getLogger(__name__).exception('detect_kind failed')
        return {'kind': DEFAULT_KIND, 'confidence': 0.0, 'evidence': [], 'source': 'heuristic'}

def all_kind_values():
    return KIND_VALUES
