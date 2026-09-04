"""Curated, source-backed "find your framework's command" reference.
Shown in the launch guide sidebar so a creator who is told "use your
framework's production build/start command" actually knows where to find it.
This is intentionally data with the same honesty rules as the guides
themselves: every command is a *documented* one, and when a command is
project-specific we say how to find it instead of guessing.
"""

FRAMEWORK_COMMANDS = (
    {
        "slug": "next-js",
        "name": "Next.js",
        "kind": "frontend",
        "how_to_find": "Look in package.json → \"scripts\". The documented build is `next build`; start the production server with `next start`.",
        "commands": (
            {"label": "Build", "text": "npm run build"},
            {"label": "Start production server", "text": "npm start"},
        ),
        "docs": {"label": "Next.js: building your application", "url": "https://nextjs.org/docs/app/building-your-application/deploying"},
    },
    {
        "slug": "react-vite",
        "name": "React (Vite)",
        "kind": "frontend",
        "how_to_find": "Vite templates put the scripts in package.json → \"scripts\". The documented build command is `vite build`; preview the built output with `vite preview`.",
        "commands": (
            {"label": "Build", "text": "npm run build"},
            {"label": "Preview the production build", "text": "npm run preview"},
        ),
        "docs": {"label": "Vite: building for production", "url": "https://vite.dev/guide/build"},
    },
    {
        "slug": "vue-vite",
        "name": "Vue (Vite)",
        "kind": "frontend",
        "how_to_find": "The Vue SFC template also uses Vite: package.json → \"scripts\" holds the documented `vite build`, and `vite preview` serves the build locally.",
        "commands": (
            {"label": "Build", "text": "npm run build"},
            {"label": "Preview the production build", "text": "npm run preview"},
        ),
        "docs": {"label": "Vite: building for production", "url": "https://vite.dev/guide/build"},
    },
    {
        "slug": "sveltekit",
        "name": "SvelteKit",
        "kind": "frontend",
        "how_to_find": "SvelteKit apps need an adapter before they are deployable. Install the adapter for your target, then `vite build` outputs the platform-specific files.",
        "commands": (
            {"label": "Build with adapter", "text": "npm run build"},
        ),
        "docs": {"label": "SvelteKit: adapters", "url": "https://svelte.dev/docs/kit/adapters"},
    },
    {
        "slug": "angular",
        "name": "Angular",
        "kind": "frontend",
        "how_to_find": "The Angular CLI documents `ng build` as the production build; the output lands in dist/ by default.",
        "commands": (
            {"label": "Build", "text": "ng build"},
        ),
        "docs": {"label": "Angular: building Angular apps", "url": "https://angular.dev/tools/cli/build"},
    },
    {
        "slug": "django",
        "name": "Django",
        "kind": "backend",
        "how_to_find": "Your project folder contains manage.py. Run `python manage.py collectstatic` after `python manage.py migrate`, then serve with Gunicorn pointing at your project's wsgi module (the folder that contains settings.py).",
        "commands": (
            {"label": "Collect static files", "text": "python manage.py collectstatic"},
            {"label": "Serve with Gunicorn", "text": "gunicorn <project>.wsgi:application", "replace": "Replace <project> with the folder that contains settings.py."},
        ),
        "docs": {"label": "Django: how to use Django with Gunicorn", "url": "https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/gunicorn/"},
    },
    {
        "slug": "flask",
        "name": "Flask",
        "kind": "backend",
        "how_to_find": "Flask's built-in `flask run` server is for development. For production use Gunicorn (or the WSGI server your host documents) pointing at the module that holds your app object.",
        "commands": (
            {"label": "Development server", "text": "flask run"},
            {"label": "Production with Gunicorn", "text": "gunicorn wsgi:app", "replace": "Replace wsgi:app with your module:app."},
        ),
        "docs": {"label": "Flask: deploy to production", "url": "https://flask.palletsprojects.com/en/stable/deploying/"},
    },
    {
        "slug": "fastapi",
        "name": "FastAPI",
        "kind": "backend",
        "how_to_find": "FastAPI is served with Uvicorn. Point it at the module containing your app instance; in production put Uvicorn behind a reverse proxy as the deployment docs describe.",
        "commands": (
            {"label": "Serve with Uvicorn", "text": "uvicorn main:app", "replace": "Replace main:app with your module:app."},
        ),
        "docs": {"label": "FastAPI: deployment", "url": "https://fastapi.tiangolo.com/deployment/"},
    },
    {
        "slug": "express-node",
        "name": "Express / Node",
        "kind": "backend",
        "how_to_find": "Node apps define their start command in package.json → \"scripts\". The production command is whatever that script runs — read it before guessing `npm start`.",
        "commands": (
            {"label": "Run the documented start script", "text": "npm start"},
        ),
        "docs": {"label": "Express: production best practices", "url": "https://expressjs.com/en/advanced/production-usage.html"},
    },
    {
        "slug": "rails",
        "name": "Ruby on Rails",
        "kind": "backend",
        "how_to_find": "Rails generates a start script in bin/. `bin/rails server` runs the dev server; a production host usually runs the same app through its own process manager.",
        "commands": (
            {"label": "Run the server", "text": "bin/rails server"},
        ),
        "docs": {"label": "Ruby on Rails: getting started", "url": "https://guides.rubyonrails.org/getting_started.html"},
    },
    {
        "slug": "laravel",
        "name": "Laravel",
        "kind": "backend",
        "how_to_find": "Laravel ships artisan commands. `php artisan serve` is for development; the deployment docs cover caching (config/routes/views) and pointing the web server at public/.",
        "commands": (
            {"label": "Development server", "text": "php artisan serve"},
            {"label": "Cache config, routes and views", "text": "php artisan optimize"},
        ),
        "docs": {"label": "Laravel: deployment", "url": "https://laravel.com/docs/deployment"},
    },
    {
        "slug": "flutter",
        "name": "Flutter",
        "kind": "mobile",
        "how_to_find": "Flutter builds per platform: `flutter build web`, `flutter build apk`/`appbundle` for Android, and the App Store/Store uploads are prepared through the release workflows in the docs.",
        "commands": (
            {"label": "Build for web", "text": "flutter build web"},
            {"label": "Build Android app bundle", "text": "flutter build appbundle"},
        ),
        "docs": {"label": "Flutter: build and release", "url": "https://docs.flutter.dev/deployment"},
    },
    {
        "slug": "godot",
        "name": "Godot",
        "kind": "game",
        "how_to_find": "Godot exports from the editor: Project → Export. Choose the platform preset, then export the release build for that target — there is no universal CLI build command across engines.",
        "commands": (),
        "docs": {"label": "Godot: exporting projects", "url": "https://docs.godotengine.org/en/stable/tutorials/export/"},        
    },
)

FRAMEWORKS_BY_SLUG = {entry["slug"]: entry for entry in FRAMEWORK_COMMANDS}

def framework_commands_for_guide(guide):
    """Return entries whose commands match the guide's audience.
    """
    blob = " ".join([
        guide.get("name", ""),
        guide.get("eyebrow", ""),
        guide.get("summary", ""),
        " ".join(guide.get("good_for", ())),
    ]).lower()
    tokens = set(blob.replace("/", " ").replace(",", " ").split())
    aliases = {
        "next": "next-js",
        "react": "react-vite",
        "vue": "vue-vite",
        "svelte": "sveltekit",
        "angular": "angular",
        "django": "django",
        "flask": "flask",
        "fastapi": "fastapi",
        "express": "express-node",
        "node": "express-node",
        "rails": "rails",
        "laravel": "laravel",
        "flutter": "flutter",
        "godot": "godot",
    }
    matched = []
    for token in tokens:
        slug = aliases.get(token)
        if slug and slug not in matched:
            matched.append(slug)
    if not matched:
        return FRAMEWORK_COMMANDS
    return tuple(FRAMEWORKS_BY_SLUG[slug] for slug in matched)
