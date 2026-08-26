"""Idempotent demo catalog so the first visit is not an empty grid."""
import io
import re
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile

from gallery.models import AppFile, AppProject, Category
from users.models import Profile

HTML_DIR = Path(settings.BASE_DIR) / 'real_templates' / 'html'

CATEGORIES = [
    ('landing-pages', 'Landing Pages', 'snippet', 1),
    ('dashboard', 'Dashboards', 'snippet', 2),
    ('track-stock', 'Track Stock', 'snippet', 3),
    ('full-apps', 'Full Apps', 'full_app', 4),
]

SNIPPETS = [
    {
        'slug': 'saas-launch-hero-pro',
        'title': 'SaaS Launch Hero Pro',
        'cat': 'landing-pages',
        'file': 'saas-launch-hero-pro.html',
        'short': 'Real Tailwind hero with nav, badge, gradient H1, two CTAs, and a social-proof bar.',
        'tech': 'HTML, Tailwind CSS',
        'stars': 18,
        'clones': 7,
        'readme': """# SaaS Launch Hero Pro

Copy-paste landing hero for a SaaS launch.

## How to use
1. Copy the HTML from the Code tab.
2. Add the Tailwind CDN script if you are not already on Tailwind.
3. Swap the product name and CTAs.

## Stack
HTML + Tailwind utility classes. No build step.
""",
    },
    {
        'slug': 'waitlist-minimal-real',
        'title': 'Waitlist Minimal',
        'cat': 'landing-pages',
        'file': 'waitlist-minimal-real.html',
        'short': 'Centered waitlist with glow mark, email field, and a single join button.',
        'tech': 'HTML, Tailwind CSS',
        'stars': 11,
        'clones': 4,
        'readme': """# Waitlist Minimal

High-converting waitlist block for a coming-soon page.

## How to use
Copy the HTML, hook the form to your email tool, keep the Tailwind classes.

## Stack
HTML + Tailwind. Form is front-end only until you wire a backend.
""",
    },
    {
        'slug': 'analytics-dashboard-real',
        'title': 'Analytics Dashboard',
        'cat': 'dashboard',
        'file': 'analytics-dashboard-real.html',
        'short': 'Sidebar, top stats, chart placeholder, and a recent-orders table.',
        'tech': 'HTML, Tailwind CSS',
        'stars': 22,
        'clones': 9,
        'readme': """# Analytics Dashboard

Admin-style dashboard snippet: sidebar, three KPI cards, chart hole, orders table.

## How to use
Copy the HTML. Drop Chart.js (or any chart) into the dashed chart area.

## Stack
HTML + Tailwind. Chart is a placeholder on purpose.
""",
    },
    {
        'slug': 'dark-saas-dashboard-real',
        'title': 'Dark SaaS Dashboard',
        'cat': 'dashboard',
        'file': 'dark-saas-dashboard-real.html',
        'short': 'Dark admin cards for revenue and expenses plus a chart area.',
        'tech': 'HTML, Tailwind CSS',
        'stars': 9,
        'clones': 3,
        'readme': """# Dark SaaS Dashboard

Dark-mode admin cards you can drop into a SaaS settings page.

## How to use
Copy the HTML. Replace the numbers with your API data.

## Stack
HTML + Tailwind. No JavaScript required for the layout.
""",
    },
    {
        'slug': 'stock-portfolio-table-real',
        'title': 'Stock Portfolio Table',
        'cat': 'track-stock',
        'file': 'stock-portfolio-table-real.html',
        'short': 'Portfolio table with symbol, price, change, sparkline, and a trade button.',
        'tech': 'HTML, Tailwind CSS',
        'stars': 15,
        'clones': 6,
        'readme': """# Stock Portfolio Table

Static portfolio table. Plug a quotes API if you want live prices.

## How to use
Copy the HTML. The LIVE pill is a style, not a live market feed.

## Stack
HTML + Tailwind. Prices in the demo are hardcoded.
""",
    },
    {
        'slug': 'crypto-chart-card-real',
        'title': 'Crypto Chart Card',
        'cat': 'track-stock',
        'file': 'crypto-chart-card-real.html',
        'short': 'Single-asset card: price, change, chart hole, buy and sell.',
        'tech': 'HTML, Tailwind CSS',
        'stars': 8,
        'clones': 2,
        'readme': """# Crypto Chart Card

One-asset trading card. Chart area is a placeholder for TradingView or Chart.js.

## How to use
Copy the HTML. Wire Buy/Sell to your own order flow.

## Stack
HTML + Tailwind. No exchange connection in this snippet.
""",
    },
]

ZIP_README = """# Stock Tracker Starter

Small full-app ZIP so you can try the stars trade path.

## What is this?
A starter Django-style project: README, app module, and requirements.

## How to run
```
pip install -r requirements.txt
python app.py
```

## Trade
This vibe costs 2 ★ to download. New accounts start with 5 ★.
"""

ZIP_APP_PY = '''"""Tiny starter — not a hosted container."""
def main():
    print("Stock tracker starter. Run locally after you trade stars for the ZIP.")


if __name__ == "__main__":
    main()
'''


def _body_html(path: Path) -> str:
    raw = path.read_text(encoding='utf-8')
    match = re.search(r'<body[^>]*>(.*)</body>', raw, re.I | re.S)
    body = match.group(1).strip() if match else raw
    # Tailwind classes need the CDN inside the sandboxed iframe.
    if 'cdn.tailwindcss.com' not in body:
        body = '<script src="https://cdn.tailwindcss.com"></script>\n' + body
    return body


def _ensure_user(username: str, password: str, stars: int = 5, **profile_kwargs):
    user, created = User.objects.get_or_create(
        username=username,
        defaults={'email': f'{username}@blaqvibes.local'},
    )
    if created:
        user.set_password(password)
        user.save()
    profile, _ = Profile.objects.get_or_create(user=user)
    updates = []
    # Only set the starter balance on first create — never reset a live wallet.
    # Ledger the demo balance too: sum(StarEvent.delta) == stars_balance must
    # hold for every wallet, seeded ones included.
    if created:
        profile.stars_balance = stars
        updates.append('stars_balance')
        if stars:
            from users.models import StarEvent
            StarEvent.objects.create(
                user=user, delta=stars, reason='admin_adjust', ref='seed-demo',
            )
        # Demo accounts count as verified — trading requires a verified email.
        if not profile.email_verified:
            profile.email_verified = True
            updates.append('email_verified')
    for key, value in profile_kwargs.items():
        if getattr(profile, key) != value:
            setattr(profile, key, value)
            updates.append(key)
    if updates:
        profile.save(update_fields=list(dict.fromkeys(updates)))
    # Demo staff need the Django flags the admin dashboard / Django admin
    # also look at. Role alone is the BlaqVibes gate; is_staff opens
    # /blaq-admin-secure/. Never flip these in production seed (roles are
    # only passed from _ensure_demo_staff, which is LOCAL_DEV-gated).
    role = profile_kwargs.get('role')
    flag_fields = []
    if role == 'superadmin':
        if not user.is_staff:
            user.is_staff = True
            flag_fields.append('is_staff')
        if not user.is_superuser:
            user.is_superuser = True
            flag_fields.append('is_superuser')
    elif role == 'admin' and not user.is_staff:
        user.is_staff = True
        flag_fields.append('is_staff')
    if flag_fields:
        user.save(update_fields=flag_fields)
    return user


def _categories():
    cats = {}
    for slug, name, typ, order in CATEGORIES:
        cat, _ = Category.objects.get_or_create(
            slug=slug,
            defaults={'name': name, 'type': typ, 'order': order},
        )
        if cat.name != name or cat.order != order:
            cat.name = name
            cat.order = order
            cat.save(update_fields=['name', 'order'])
        cats[slug] = cat
    return cats


def _seed_snippets(owner, cats):
    created = 0
    for item in SNIPPETS:
        path = HTML_DIR / item['file']
        if not path.exists():
            continue
        html = _body_html(path)
        project, was_created = AppProject.objects.get_or_create(
            slug=item['slug'],
            defaults={
                'owner': owner,
                'title': item['title'],
                'category': cats[item['cat']],
                'short_description': item['short'],
                'readme': item['readme'],
                'tech_stack': item['tech'],
                'html_code': html,
                'css_code': '/* Tailwind via CDN inside the sandboxed preview iframe. */',
                'status': 'published',
                'file_count': 1,
                'stars': item['stars'],
                'clones': item['clones'],
                'language_stats': {'HTML': 80, 'CSS': 20},
            },
        )
        if was_created:
            created += 1
        elif project.status != 'published':
            project.status = 'published'
            project.save(update_fields=['status'])
    return created


def _seed_zip_app(owner, cats):
    slug = 'stock-tracker-starter'
    if AppProject.objects.filter(slug=slug).exists():
        project = AppProject.objects.get(slug=slug)
        if project.status != 'published':
            project.status = 'published'
            project.save(update_fields=['status'])
        return 0

    buf = io.BytesIO()
    files = {
        'README.md': ZIP_README,
        'app.py': ZIP_APP_PY,
        'requirements.txt': 'Django>=5.0\n',
    }
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    buf.seek(0)

    project = AppProject(
        owner=owner,
        title='Stock Tracker Starter',
        slug=slug,
        category=cats['full-apps'],
        short_description='Full-app ZIP. Trade 2 ★ to download and run it on your machine.',
        readme=ZIP_README,
        tech_stack='Python, Django',
        star_cost=2,
        status='published',
        file_count=3,
        stars=6,
        clones=2,
        language_stats={'Python': 70, 'Markdown': 30},
        file_tree={'README.md': {}, 'app.py': {}, 'requirements.txt': {}},
    )
    project.save()
    project.zip_file.save(f'{slug}.zip', ContentFile(buf.read()), save=True)
    for name, content in files.items():
        AppFile.objects.get_or_create(
            project=project,
            path=name,
            defaults={'size': len(content.encode('utf-8'))},
        )
    return 1


def _ensure_demo_staff():
    """Local/debug only — the accounts the docs already tell people to use.

    5 Whys: why here? Specs and the admin demo say "login nolo.ai /
    blaq12345" and "blaq is admin." seed_demo used to create those
    usernames as role='user', so the documented password signed in and
    then 403'd on every admin page. That is the "admin login never
    works" ticket. Production seed stays role='user' — a known-password
    superadmin must never ship with SEED_DEMO=1 on a public host.
    """
    _ensure_user('blaq', 'blaq12345', stars=20, role='admin')
    _ensure_user('thando', 'thando12345', stars=12, role='moderator')
    _ensure_user('nolo.ai', 'blaq12345', stars=42, role='superadmin')
    try:
        from users.provision import repair_createsuperuser_admin
        repair_createsuperuser_admin()
    except Exception:
        import logging
        logging.getLogger(__name__).exception('repair createsuperuser admin failed')


def seed_demo():
    """Create published demo vibes. Safe to run many times."""
    owner = _ensure_user('blaq', 'blaq12345', stars=20)
    _ensure_user('thando', 'thando12345', stars=12)
    if getattr(settings, 'LOCAL_DEV', False) or getattr(settings, 'DEBUG', False):
        _ensure_demo_staff()
    try:
        from users.provision import maybe_provision_from_env
        maybe_provision_from_env()
    except Exception:
        import logging
        logging.getLogger(__name__).exception('env superadmin provision failed')
    cats = _categories()
    created = _seed_snippets(owner, cats)
    created += _seed_zip_app(owner, cats)
    # Label and score the demo catalog too. Why? A fresh install would
    # otherwise show every demo vibe as kind='other' with appeal 0, which
    # looks exactly like the discovery feature being broken. Heuristic only:
    # seeding must work offline and cost nothing.
    try:
        from .classify import classify_project
        from .interest import refresh_project
        for project in AppProject.objects.filter(status='published'):
            classify_project(project, allow_llm=False)
            refresh_project(project)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('seed classify failed')
    published = AppProject.objects.filter(status='published').count()
    return {'created': created, 'published': published}
