"""Heuristic program-kind detection — the deterministic floor.
`classify.classify_project()` is the public entry point; this module is the
part that must work with **no API key, no network, and no money**.

Design follows the same strategy chain that makes GitHub's linguist
accurate (deep research: docs/specs/BlaqVibes_Detection_Research.md):

1. near-proof FILE fingerprints (project.godot, AndroidManifest.xml, …)
2. directory + extension fingerprints, depth-discounted
3. dependency MANIFEST fingerprints — reading package.json /
   requirements.txt / pubspec.yaml / Cargo.toml / go.mod content from the
   ZIP and matching known packages to kinds (the Wappalyzer idea: what a
   project *depends on* is a better statement of what it is than any word
   in its README)
4. free-text words in title/description/stack/README, capped per term
5. shape corrections — facts about the artifact that beat prose

The LLM in `classify.py` is the last stage for genuinely ambiguous rows —
the same role linguist's Bayesian classifier plays.
"""
import json
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
    'steam_appid.txt': [('game', 4)],
    'pubspec.yaml': [('mobile_app', 4)],
    # mobile
    'androidmanifest.xml': [('mobile_app', 5)],
    'mainactivity.java': [('mobile_app', 5)],
    'mainactivity.kt': [('mobile_app', 5)],
    'main.storyboard': [('mobile_app', 4)],
    'info.plist': [('mobile_app', 3)],
    'podfile': [('mobile_app', 4)],
    'app.json': [('mobile_app', 2)],
    'capacitor.config.json': [('mobile_app', 4)],
    'capacitor.config.ts': [('mobile_app', 4)],
    # NOTE: build.gradle / build.gradle.kts are NOT here on purpose. Gradle
    # alone cannot tell an Android app from a JVM server; the combo rule in
    # score_kinds() decides using the presence (or absence) of Android
    # markers. That was the single worst misclassification this module had.
    # desktop
    'main.qml': [('desktop_app', 4)],
    'tauri.conf.json': [('desktop_app', 5)],
    'electron.js': [('desktop_app', 4)],
    'electron-builder.yml': [('desktop_app', 5)],
    'forge.config.js': [('desktop_app', 4)],
    'cmakelists.txt': [('desktop_app', 2)],
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
    'pom.xml': [('api_backend', 2)],
    'composer.json': [('api_backend', 3)],
    'gemfile': [('api_backend', 2)],
    'wrangler.toml': [('api_backend', 3)],
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
    'makefile': [('cli_tool', 1)],
    'main.rs': [('cli_tool', 2)],
    # library
    'setup.py': [('library', 3)],
    'pyproject.toml': [('library', 2)],
    'cargo.toml': [('library', 1)],
    'lib.rs': [('library', 2)],
    'package.swift': [('library', 3)],
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
    'vercel.json': [('web_app', 3)],
    'netlify.toml': [('web_app', 3)],
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
    'cmd': [('cli_tool', 2)],
    'ios': [('mobile_app', 3)],
    'android': [('mobile_app', 3)],
    'xcassets': [('mobile_app', 2)],
}

# File extensions.
_EXT_SIGNALS = {
    '.unity': [('game', 5)],
    '.prefab': [('game', 5)],
    '.tscn': [('game', 5)],
    '.gd': [('game', 5)],
    '.gml': [('game', 4)],
    '.blend': [('game', 3)],
    '.lua': [('game', 2)],
    '.apk': [('mobile_app', 5)],
    '.aab': [('mobile_app', 5)],
    '.ipa': [('mobile_app', 5)],
    '.xcodeproj': [('mobile_app', 5)],
    '.pbxproj': [('mobile_app', 4)],
    '.storyboard': [('mobile_app', 4)],
    '.entitlements': [('mobile_app', 2)],
    '.dart': [('mobile_app', 4)],
    '.swift': [('mobile_app', 3)],
    '.kt': [('mobile_app', 2)],
    '.m': [('mobile_app', 3)],
    '.mm': [('mobile_app', 4)],
    '.ipynb': [('ai_ml', 4), ('data_viz', 2)],
    '.pkl': [('ai_ml', 3)],
    '.h5': [('ai_ml', 3)],
    '.onnx': [('ai_ml', 4)],
    '.pt': [('ai_ml', 3)],
    '.csv': [('data_viz', 2)],
    '.parquet': [('data_viz', 3)],
    '.r': [('data_viz', 2)],
    '.jl': [('data_viz', 2)],
    '.ino': [('other', 3)],
    '.vue': [('web_app', 2)],
    '.svelte': [('web_app', 3)],
    '.astro': [('web_app', 3)],
    '.jsx': [('web_app', 2)],
    '.tsx': [('web_app', 2)],
    '.html': [('static_site', 1)],
    # Native source: weak on purpose — C/C++ can be a desktop app, a game,
    # firmware or a library; only near-proof files (or README words) may
    # pull it one way.
    '.cs': [('desktop_app', 1)],
    '.cpp': [('desktop_app', 1)],
    '.cc': [('desktop_app', 1)],
    '.c': [('desktop_app', 1)],
    '.hpp': [('desktop_app', 1)],
    '.csproj': [('desktop_app', 1)],
    '.sln': [('desktop_app', 1)],
    '.ex': [('api_backend', 1)],
    '.exs': [('api_backend', 1)],
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
    'spring boot': [('api_backend', 4)],
    'asp.net': [('api_backend', 3)],
    'rails': [('api_backend', 4)],
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
    'vscode extension': [('extension', 5)],
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

# Detected-language share biases. Weights are small: a language narrows the
# candidates, it never decides alone (Python is a web backend, a CLI, a data
# pipeline or a game with equal ease).
_LANGUAGE_SIGNALS = {
    'Python': [('api_backend', 1), ('cli_tool', 1)],
    'JavaScript': [('web_app', 1)],
    'TypeScript': [('web_app', 1)],
    'HTML': [('static_site', 1)],
    'CSS': [('static_site', 1)],
    'Vue': [('web_app', 2)],
    'Svelte': [('web_app', 2)],
    'Astro': [('web_app', 2)],
    'Swift': [('mobile_app', 2)],
    'Objective-C': [('mobile_app', 3)],
    'Kotlin': [('mobile_app', 2)],
    'Java': [('api_backend', 1), ('mobile_app', 1)],
    'Go': [('api_backend', 1), ('cli_tool', 1)],
    'Rust': [('cli_tool', 1)],
    'PHP': [('api_backend', 1)],
    'Ruby': [('api_backend', 1)],
    'Scala': [('api_backend', 1)],
    'Elixir': [('api_backend', 2)],
    'C': [('desktop_app', 1)],
    'C++': [('desktop_app', 1)],
    'C#': [('desktop_app', 1)],
    'Dart': [('mobile_app', 2)],
    'Lua': [('game', 1)],
    'R': [('data_viz', 2)],
    'Julia': [('data_viz', 1)],
    'Shell': [('cli_tool', 2)],
    'PowerShell': [('cli_tool', 1)],
    'Batchfile': [('cli_tool', 1)],
    'Haskell': [('cli_tool', 1)],
    'Perl': [('cli_tool', 1)],
    'Zig': [('cli_tool', 1)],
    'Nim': [('cli_tool', 1)],
    'Dockerfile': [('api_backend', 1)],
    'Makefile': [('cli_tool', 1)],
    'CMake': [('desktop_app', 1)],
}

# ---------------------------------------------------------------------------
# Manifest fingerprints — the Wappalyzer idea, applied to source zips.
# A dependency list is a far more honest "what is this" than README prose,
# and it is the only signal that can tell an Electron app from a web app or
# an Express API from a Next.js site when all four ship a package.json.
# ---------------------------------------------------------------------------

_MANIFEST_NAMES = (
    'package.json', 'requirements.txt', 'pyproject.toml', 'pipfile',
    'pubspec.yaml', 'cargo.toml', 'go.mod', 'composer.json', 'gemfile',
)
_MANIFEST_MAX_BYTES = 128 * 1024
_MAX_MANIFESTS = 6
_MAX_RAW_PATHS = 2000

# name-prefix fingerprints: dep name (exact or prefix) -> kind, weight.
_NPM_SIGNALS = {
    'electron': ('desktop_app', 5),
    'electron-builder': ('desktop_app', 4),
    '@tauri-apps/api': ('desktop_app', 5),
    'tauri': ('desktop_app', 5),
    'react-native': ('mobile_app', 5),
    'react-native-': ('mobile_app', 5),
    '@react-native/': ('mobile_app', 5),
    '@react-native-': ('mobile_app', 5),
    'expo': ('mobile_app', 5),
    '@expo/': ('mobile_app', 4),
    'next': ('web_app', 4),
    'nuxt': ('web_app', 4),
    '@sveltejs/kit': ('web_app', 4),
    'astro': ('web_app', 4),
    '@angular/core': ('web_app', 4),
    'gatsby': ('web_app', 4),
    '@remix-run/': ('web_app', 4),
    'react': ('web_app', 3),
    'react-dom': ('web_app', 3),
    'vue': ('web_app', 3),
    'svelte': ('web_app', 3),
    'vite': ('web_app', 2),
    'express': ('api_backend', 4),
    'fastify': ('api_backend', 4),
    'koa': ('api_backend', 4),
    '@nestjs/': ('api_backend', 5),
    'hono': ('api_backend', 4),
    '@hono/': ('api_backend', 4),
    'socket.io': ('api_backend', 3),
    'apollo-server': ('api_backend', 4),
    'discord.js': ('bot', 5),
    '@discordjs/': ('bot', 5),
    'discordeno': ('bot', 5),
    'eris': ('bot', 3),
    'telegraf': ('bot', 5),
    'telebot': ('bot', 5),
    'node-telegram-bot-api': ('bot', 5),
    'whatsapp-web.js': ('bot', 5),
    'matrix-bot-sdk': ('bot', 5),
    'twilio': ('bot', 3),
    'puppeteer': ('bot', 4),
    'puppeteer-core': ('bot', 3),
    'playwright': ('bot', 4),
    '@playwright/': ('bot', 4),
    'selenium-webdriver': ('bot', 4),
    'cheerio': ('bot', 3),
    'commander': ('cli_tool', 4),
    'yargs': ('cli_tool', 4),
    'cac': ('cli_tool', 3),
    'meow': ('cli_tool', 4),
    'oclif': ('cli_tool', 5),
    'inquirer': ('cli_tool', 3),
    '@clack/': ('cli_tool', 3),
}
_PYPI_SIGNALS = {
    'django': ('api_backend', 4),
    'djangorestframework': ('api_backend', 4),
    'flask': ('api_backend', 4),
    'fastapi': ('api_backend', 4),
    'bottle': ('api_backend', 3),
    'tornado': ('api_backend', 3),
    'sanic': ('api_backend', 3),
    'starlette': ('api_backend', 3),
    'gunicorn': ('api_backend', 3),
    'uvicorn': ('api_backend', 3),
    'torch': ('ai_ml', 5),
    'torchvision': ('ai_ml', 5),
    'tensorflow': ('ai_ml', 5),
    'keras': ('ai_ml', 5),
    'transformers': ('ai_ml', 5),
    'scikit-learn': ('ai_ml', 5),
    'sklearn': ('ai_ml', 4),
    'xgboost': ('ai_ml', 4),
    'lightgbm': ('ai_ml', 4),
    'openai': ('ai_ml', 4),
    'langchain': ('ai_ml', 5),
    'llama-index': ('ai_ml', 5),
    'diffusers': ('ai_ml', 5),
    'accelerate': ('ai_ml', 3),
    'spacy': ('ai_ml', 3),
    'nltk': ('ai_ml', 3),
    'streamlit': ('data_viz', 5),
    'gradio': ('data_viz', 5),
    'dash': ('data_viz', 5),
    'panel': ('data_viz', 3),
    'plotly': ('data_viz', 3),
    'matplotlib': ('data_viz', 3),
    'seaborn': ('data_viz', 3),
    'bokeh': ('data_viz', 3),
    'altair': ('data_viz', 3),
    'pandas': ('data_viz', 2),
    'numpy': ('data_viz', 2),
    'polars': ('data_viz', 2),
    'pyspark': ('data_viz', 2),
    'dask': ('data_viz', 2),
    'scrapy': ('bot', 4),
    'selenium': ('bot', 4),
    'beautifulsoup4': ('bot', 3),
    'bs4': ('bot', 3),
    'python-telegram-bot': ('bot', 5),
    'pytelegrambotapi': ('bot', 5),
    'telebot': ('bot', 5),
    'discord.py': ('bot', 5),
    'discord': ('bot', 4),
    'tweepy': ('bot', 3),
    'click': ('cli_tool', 3),
    'typer': ('cli_tool', 4),
    'textual': ('cli_tool', 4),
    'rich': ('cli_tool', 2),
    'fire': ('cli_tool', 3),
    'kivy': ('desktop_app', 4),
    'pyqt5': ('desktop_app', 4),
    'pyqt6': ('desktop_app', 4),
    'pyside2': ('desktop_app', 4),
    'pyside6': ('desktop_app', 4),
    'flet': ('desktop_app', 4),
    'wxpython': ('desktop_app', 4),
    'pygame': ('game', 5),
    'arcade': ('game', 5),
    'pyglet': ('game', 5),
    'panda3d': ('game', 5),
}
# Text markers inside non-npm/non-pypi manifests.
_CARGO_MARKERS = (
    ('actix-web', 'api_backend', 4), ('axum', 'api_backend', 4),
    ('rocket', 'api_backend', 4), ('warp', 'api_backend', 4),
    ('bevy', 'game', 5), ('ggez', 'game', 5), ('macroquad', 'game', 5),
    ('tauri', 'desktop_app', 5), ('clap', 'cli_tool', 3),
)
_GO_MARKERS = (
    ('gin-gonic', 'api_backend', 4), ('/echo', 'api_backend', 3),
    ('gofiber', 'api_backend', 4), ('/fiber', 'api_backend', 3),
    ('spf13/cobra', 'cli_tool', 4), ('spf13/viper', 'cli_tool', 2),
    ('urfave/cli', 'cli_tool', 4),
)
_COMPOSER_MARKERS = (
    ('laravel/framework', 'api_backend', 4), ('symfony/', 'api_backend', 3),
)
_GEMFILE_MARKERS = (
    ('rails', 'api_backend', 5), ('sinatra', 'api_backend', 4),
    ('jekyll', 'static_site', 5),
)
_PUBSPEC_MARKERS = (
    ('flutter:', 'mobile_app', 5), ('sdk: flutter', 'mobile_app', 5),
    ('flame', 'game', 4),
)

# Max evidence a single source may contribute, so a README that repeats
# "game" forty times cannot outvote the actual file tree.
_TEXT_CAP_PER_TERM = 1
_MAX_PATHS = 400

def _strip_common_root(paths):
    """Drop the `<repo>-<ref>/` wrapper GitHub zips add (all paths only)."""
    if len(paths) < 2:
        return paths
    firsts = {p.split('/', 1)[0] for p in paths}
    if len(firsts) == 1 and all('/' in p for p in paths):
        return [p.split('/', 1)[1] for p in paths]
    return paths

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
    paths = _strip_common_root(paths)
    try:
        project._kind_paths_cache = paths
    except Exception:
        pass
    return paths

def _raw_paths(project):
    """Original-case archive paths for manifest reading (not for scoring)."""
    cached = getattr(project, '_kind_raw_paths_cache', None)
    if cached is not None:
        return cached
    raw = []
    try:
        for f in project.files.all()[:_MAX_RAW_PATHS]:
            p = (f.path or '').replace('\\', '/').strip('/')
            if p:
                raw.append(p)
    except Exception:
        pass
    try:
        project._kind_raw_paths_cache = raw
    except Exception:
        pass
    return raw

def _read_manifests(project):
    """Small text manifests read from the archive, {lowered name: text}.

    One zip open per classification (cached on the project instance), one
    file per manifest name, capped in size and count. Vendored and lock
    paths are skipped — `node_modules/.../package.json` says nothing about
    the project. Never raises: a manifest is a bonus, not a requirement.
    """
    cached = getattr(project, '_kind_manifest_cache', None)
    if cached is not None:
        return cached
    out = {}
    if not getattr(project, 'zip_file', None):
        try:
            project._kind_manifest_cache = out
        except Exception:
            pass
        return out
    try:
        from .language import _VENDOR_RE, _LOCKFILE_RE
        from .ziputil import open_zip
        wanted = {}
        for p in _raw_paths(project):
            lower = p.lower()
            base = lower.rsplit('/', 1)[-1]
            if base in _MANIFEST_NAMES and not _VENDOR_RE.search(lower) \
                    and not _LOCKFILE_RE.search(lower):
                depth = lower.count('/')
                # shallowest match wins: the project's own manifest
                if base not in wanted or depth < wanted[base][0]:
                    wanted[base] = (depth, p)
        with open_zip(project.zip_file) as z:
            names = {i.filename.replace('\\', '/'): i for i in z.infolist()}
            for base, (_depth, orig) in sorted(wanted.items(), key=lambda kv: kv[1][0]):
                if len(out) >= _MAX_MANIFESTS:
                    break
                info = names.get(orig)
                if info is None or info.is_dir() or info.file_size > _MANIFEST_MAX_BYTES:
                    continue
                with z.open(info) as fh:
                    out[base] = fh.read(_MANIFEST_MAX_BYTES).decode('utf-8', 'ignore')
    except Exception:
        pass
    try:
        project._kind_manifest_cache = out
    except Exception:
        pass
    return out

def _dep_names(requirements_text):
    """Loose tokenisation of requirements/pyproject-style dependency text."""
    names = set()
    for line in requirements_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('['):
            continue
        m = re.match(r'^[-\s]*([A-Za-z0-9][A-Za-z0-9._-]*)', line)
        if m:
            names.add(m.group(1).lower().replace('_', '-'))
        # pyproject table form: "django = "^4.2"" / '"flask>=2"'
        m = re.match(r'^["\']?([A-Za-z0-9][A-Za-z0-9._-]*)["\']?\s*[=<>~^]', line)
        if m:
            names.add(m.group(1).lower().replace('_', '-'))
    return names

def _match_dep(names, table):
    """Best (kind, weight, label) per kind for a set of dependency names."""
    hits = {}
    for name in names:
        for sig, (kind, weight) in table.items():
            if name == sig or (sig.endswith(('/', '-')) and name.startswith(sig)):
                if kind not in hits or hits[kind][0] < weight:
                    hits[kind] = (weight, name)
    return hits

def _manifest_scores(project):
    """[(kind, weight, why)] from manifest fingerprints. Never raises."""
    scores = []
    try:
        manifests = _read_manifests(project)

        pkg_text = manifests.get('package.json')
        if pkg_text:
            try:
                pkg = json.loads(pkg_text)
            except Exception:
                pkg = None
            if isinstance(pkg, dict):
                deps = set()
                for block in ('dependencies', 'devDependencies',
                              'peerDependencies', 'optionalDependencies'):
                    block_val = pkg.get(block)
                    if isinstance(block_val, dict):
                        deps.update(str(k).lower() for k in block_val)
                for kind, (weight, name) in _match_dep(deps, _NPM_SIGNALS).items():
                    scores.append((kind, weight, f'package.json uses {name}'))
                if pkg.get('bin'):
                    scores.append(('cli_tool', 3, 'bin entry in package.json'))
                    scores.append(('library', 2, 'bin entry in package.json'))
                build = pkg.get('build')
                if isinstance(build, dict) and build.get('appId'):
                    scores.append(('desktop_app', 2, 'electron-builder config'))

        req_names = set()
        for req_name in ('requirements.txt', 'pyproject.toml', 'pipfile'):
            text = manifests.get(req_name)
            if text:
                req_names |= _dep_names(text)
        if req_names:
            for kind, (weight, name) in _match_dep(req_names, _PYPI_SIGNALS).items():
                scores.append((kind, weight, f'python deps include {name}'))

        cargo = manifests.get('cargo.toml')
        if cargo:
            for marker, kind, weight in _CARGO_MARKERS:
                if marker in cargo:
                    scores.append((kind, weight, f'cargo.toml uses {marker}'))
            if re.search(r'^\s*\[\[bin\]\]', cargo, re.MULTILINE):
                scores.append(('cli_tool', 2, 'cargo [[bin]] target'))

        gomod = manifests.get('go.mod')
        if gomod:
            for marker, kind, weight in _GO_MARKERS:
                if marker in gomod:
                    scores.append((kind, weight, f'go.mod uses {marker}'))

        composer = manifests.get('composer.json')
        if composer:
            for marker, kind, weight in _COMPOSER_MARKERS:
                if marker in composer:
                    scores.append((kind, weight, f'composer.json uses {marker}'))

        gemfile = manifests.get('gemfile')
        if gemfile:
            for marker, kind, weight in _GEMFILE_MARKERS:
                if marker in gemfile:
                    scores.append((kind, weight, f'gemfile uses {marker}'))

        pubspec = manifests.get('pubspec.yaml')
        if pubspec:
            for marker, kind, weight in _PUBSPEC_MARKERS:
                if marker in pubspec:
                    scores.append((kind, weight, f'pubspec.yaml uses {marker}'))
    except Exception:
        return []
    # One strongest fingerprint per kind per manifest family — ten AI deps
    # must not stack into certainty.
    best = {}
    for kind, weight, why in scores:
        if kind not in best or best[kind][0] < weight:
            best[kind] = (weight, why)
    return [(k, w, why) for k, (w, why) in best.items()]

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
    names_seen = set()
    dirs_seen = set()

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
            names_seen.add(base)
            for kind, weight in _NAME_SIGNALS.get(base, ()):
                _add(scores, evidence, kind, weight * factor, f'file {base}')
        for seg in segments[:-1]:
            key = ('dir', seg)
            if key in seen_names:
                continue
            seen_names.add(key)
            dirs_seen.add(seg)
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

    # Combo rule: gradle alone is ambiguous (JVM server vs Android app);
    # the presence or absence of Android markers decides it.
    gradle = {'build.gradle', 'build.gradle.kts', 'settings.gradle'} & names_seen
    if gradle:
        android = (
            'androidmanifest.xml' in names_seen
            or 'android' in dirs_seen
            or 'mainactivity.java' in names_seen
            or 'mainactivity.kt' in names_seen
        )
        if android:
            _add(scores, evidence, 'mobile_app', 4, 'gradle + android markers')
        else:
            _add(scores, evidence, 'api_backend', 3, 'gradle, no android markers')

    # Manifest fingerprints — stronger than prose, weaker than near-proof
    # file evidence, and immune to README marketing.
    for kind, weight, why in _manifest_scores(project):
        _add(scores, evidence, kind, weight, why)

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
