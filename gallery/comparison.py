"""Comparison matrix for the Launch hub — "which platform do I pick?".

Curated rows reference guides by slug (never a parallel set of claims) and
add one *cost* label per platform. Cost labels are deliberately coarse and
were checked against each platform's pricing in August 2026; the live docs
remain the authority and the row links to its guide.

5 Whys:
1. Why a matrix at all? The hub lists 22 destinations; a creator deciding
   between Cloudflare/Netlify/Vercel or Render/Railway/Fly has no signal.
   The matrix turns "22 cards" into "3 comparable choices".
2. Why reference guides by slug instead of duplicating name/pace/best-for?
   One source of truth: the guide data already holds pace, good_for and the
   doc links, and the view joins them. Duplicating text would drift.
3. Why a separate cost field and not a claim inside each guide? Pricing is
   the fastest-moving fact. Keeping it in ONE curated table makes the
   quarterly recheck a single diff instead of 22 edits, and the matrix is
   the only surface that needs the label.
4. Why coarse labels ("Free tier", "Trial credits") and not exact prices?
   Exact numbers change and a wrong "US$5/mo" is a fabricated claim. The
   coarse label survives a price change, and the row links to live docs.
5. Why on the hub and not the guide pages? The decision is made BEFORE
   opening a guide; the hub is where the user is choosing. Guide pages stay
   focused on how-to.

Maintenance: when a platform's pricing model changes, update ONLY the cost
label here, then bump that guide's `last_reviewed` in launch_guides.py.
"""

COMPARISON_GROUPS = (
    {
        "slug": "static-hosts",
        "label": "Static site hosts",
        "question": "I have a folder or frontend build with index.html",
        "rows": (
            {"slug": "cloudflare-pages", "cost": "Free tier"},
            {"slug": "vercel-web", "cost": "Free tier"},
            {"slug": "netlify", "cost": "Free tier"},
            {"slug": "aws-s3-cloudfront", "cost": "Free allowance, then pay-as-you-go"},
        ),
    },
    {
        "slug": "server-hosts",
        "label": "Server / full-stack hosts",
        "question": "I have an API, Django app, or app with a database",
        "rows": (
            {"slug": "render-web-service", "cost": "Free tier (spins down when idle)"},
            {"slug": "digitalocean-app-platform", "cost": "Free tier (static sites); paid from ~US$5/mo"},
            {"slug": "railway", "cost": "Trial credits, then paid"},
            {"slug": "fly-io", "cost": "No free tier — trial, then paid"},
            {"slug": "google-cloud-run", "cost": "Free tier"},
            {"slug": "pythonanywhere", "cost": "Free tier"},
            {"slug": "supabase", "cost": "Free tier (managed backend)"},
        ),
    },
    {
        "slug": "container-routes",
        "label": "Container routes",
        "question": "I have a Dockerfile or container image",
        "rows": (
            {"slug": "docker-hub", "cost": "Free tier — registry only, not a host"},
            {"slug": "railway", "cost": "Trial credits, then paid"},
            {"slug": "fly-io", "cost": "No free tier — trial, then paid"},
            {"slug": "google-cloud-run", "cost": "Free tier"},
        ),
    },
    {
        "slug": "game-routes",
        "label": "Game routes",
        "question": "I have a game — where does it go?",
        "rows": (
            {"slug": "itchio", "cost": "Free to publish"},
            {"slug": "steam", "cost": "US$100 per product fee"},
        ),
    },
    {
        "slug": "mobile-stores",
        "label": "Mobile stores",
        "question": "I have a mobile app build",
        "rows": (
            {"slug": "google-play", "cost": "US$25 one-time developer fee"},
            {"slug": "apple-app-store", "cost": "US$99/year developer membership"},
        ),
    },
    {
        "slug": "desktop-routes",
        "label": "Desktop routes",
        "question": "I have a desktop app — Windows, macOS, or Linux",
        "rows": (
            {"slug": "microsoft-store", "cost": "US$19 one-time developer fee"},
            {"slug": "macos-direct", "cost": "Requires Apple Developer (US$99/yr)"},
            {"slug": "flathub", "cost": "Free to publish (open source)"},
        ),
    },
)

COMPARISON_BY_GROUP = {group["slug"]: group for group in COMPARISON_GROUPS}


def enrich_comparison_groups(guide_by_slug):
    """Join comparison rows with guide data for the hub template.

    5 Whys:
    1. Why join here instead of storing full rows in comparison.py? The
       guide dict is the single source for name/pace/eyebrow/good_for; the
       matrix stores only slug + cost so nothing can drift.
    2. Why return the guide in the row? The template needs the destination
       link and label, and rendering already annotates guides elsewhere.
    3. Why skip a row whose guide is missing? A typo'd slug would crash the
       hub; skipping keeps the hub alive and a test catches the typo.
    4. Why first two good_for items as "best for"? The matrix is a glance;
       a long list defeats it, and the guide page has the full list.
    5. Why keep group question text here? It is the decision prompt that
       makes the matrix a chooser, not a table — copy belongs with data.
    """
    enriched = []
    for group in COMPARISON_GROUPS:
        rows = []
        for row in group["rows"]:
            guide = guide_by_slug.get(row["slug"])
            # A guide missing any field the template needs is treated like a
            # missing guide: skip the row, keep the hub alive, let the tests
            # catch the data typo.
            if guide is None:
                continue
            if not all(k in guide for k in ("slug", "name", "icon", "pace")):
                continue
            rows.append({
                "slug": guide["slug"],
                "name": guide["name"],
                "icon": guide.get("icon", ""),
                "pace": guide.get("pace", ""),
                "eyebrow": guide.get("eyebrow", ""),
                "best_for": guide.get("good_for", ())[:2],
                "cost": row["cost"],
            })
        enriched.append({**group, "rows": rows})
    return tuple(enriched)
