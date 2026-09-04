"""Program kinds — one canonical table for "what sort of program is this?".
Everything downstream (detection, LLM classification, feed filters, taste
learning, badges) reads THIS table. Nothing else is allowed to invent a
kind string.
"""

# preview values:
#   'snippet' — can run in the sandboxed iframe when html_code is present
#   'files'   — file list + README only; there is no way to run it here
PROGRAM_KINDS = (
    {
        'value': 'game',
        'label': 'Game',
        'icon': '🎮',
        'blurb': 'Playable game — browser, engine project, or game source.',
        'preview': 'snippet',
        'web_native': True,
    },
    {
        'value': 'web_app',
        'label': 'Web app',
        'icon': '🌐',
        'blurb': 'Interactive app that runs in a browser.',
        'preview': 'snippet',
        'web_native': True,
    },
    {
        'value': 'static_site',
        'label': 'Website',
        'icon': '📄',
        'blurb': 'Landing page, portfolio, or docs site.',
        'preview': 'snippet',
        'web_native': True,
    },
    {
        'value': 'api_backend',
        'label': 'API / backend',
        'icon': '🧩',
        'blurb': 'Server, API, or backend service. Runs on your machine.',
        'preview': 'files',
        'web_native': False,
    },
    {
        'value': 'mobile_app',
        'label': 'Mobile app',
        'icon': '📱',
        'blurb': 'Android or iOS app. Build it in your own toolchain.',
        'preview': 'files',
        'web_native': False,
    },
    {
        'value': 'desktop_app',
        'label': 'Desktop app',
        'icon': '🖥️',
        'blurb': 'Windows, macOS, or Linux desktop program.',
        'preview': 'files',
        'web_native': False,
    },
    {
        'value': 'ai_ml',
        'label': 'AI / ML',
        'icon': '🧠',
        'blurb': 'Model, notebook, agent, or AI-powered tool.',
        'preview': 'files',
        'web_native': False,
    },
    {
        'value': 'bot',
        'label': 'Bot / automation',
        'icon': '⚙️',
        'blurb': 'Chat bot, scraper, or scheduled automation.',
        'preview': 'files',
        'web_native': False,
    },
    {
        # web_native: a dashboard is very often exactly an HTML page with
        # charts in it, so a pasted snippet claiming to be one is credible —
        # unlike a snippet claiming to be an Android app.
        'value': 'data_viz',
        'label': 'Data / dashboard',
        'icon': '📊',
        'blurb': 'Analytics, charts, notebooks, or reporting.',
        'preview': 'snippet',
        'web_native': True,
    },
    {
        'value': 'cli_tool',
        'label': 'CLI / script',
        'icon': '⌨️',
        'blurb': 'Command-line tool or utility script.',
        'preview': 'files',
        'web_native': False,
    },
    {
        'value': 'library',
        'label': 'Library / SDK',
        'icon': '📦',
        'blurb': 'Package other developers import into their own code.',
        'preview': 'files',
        'web_native': False,
    },
    {
        'value': 'extension',
        'label': 'Browser extension',
        'icon': '🧷',
        'blurb': 'Chrome / Firefox extension. Load it unpacked to try it.',
        'preview': 'files',
        'web_native': False,
    },
    {
        'value': 'template',
        'label': 'Template / UI kit',
        'icon': '🎨',
        'blurb': 'Starter, boilerplate, theme, or component kit.',
        'preview': 'snippet',
        'web_native': True,
    },
    {
        'value': 'other',
        'label': 'Other',
        'icon': '✳️',
        'blurb': 'Published as-is — download the files to run it.',
        'preview': 'files',
        'web_native': False,
    },
)

KIND_VALUES = tuple(k['value'] for k in PROGRAM_KINDS)
KIND_CHOICES = tuple((k['value'], k['label']) for k in PROGRAM_KINDS)
KIND_BY_VALUE = {k['value']: k for k in PROGRAM_KINDS}
DEFAULT_KIND = 'other'

# Kinds a creator may pick by hand on the publish form, plus auto-detect.
UPLOAD_KIND_CHOICES = (('', 'Auto-detect (recommended)'),) + KIND_CHOICES

PREVIEW_MODES = (
    ('snippet', 'Runs in a sandboxed preview'),
    ('static_zip', 'Static site runs in a sandboxed preview'),
    ('files', 'File list + README only'),
)

# The two modes that really execute in the sandboxed iframe. Kept as one
# tuple so the model, the feed filter, and the API agree on "runnable".
RUNNABLE_PREVIEW_MODES = ('snippet', 'static_zip')

def coerce_kind(value):
    """Return a valid kind value, or DEFAULT_KIND.

    Why here and not at each call site? Three producers write this field —
    the heuristic detector, the LLM, and the creator's own dropdown. A
    single funnel means an unknown string can never reach the database.
    """
    if not value:
        return DEFAULT_KIND
    v = str(value).strip().lower().replace('-', '_').replace(' ', '_')
    if v in KIND_BY_VALUE:
        return v
    # Tolerate the handful of near-misses an LLM actually produces.
    aliases = {
        'games': 'game',
        'gaming': 'game',
        'webapp': 'web_app',
        'web': 'web_app',
        'website': 'static_site',
        'site': 'static_site',
        'landing_page': 'static_site',
        'frontend': 'web_app',
        'backend': 'api_backend',
        'api': 'api_backend',
        'server': 'api_backend',
        'mobile': 'mobile_app',
        'android': 'mobile_app',
        'ios': 'mobile_app',
        'desktop': 'desktop_app',
        'ai': 'ai_ml',
        'ml': 'ai_ml',
        'machine_learning': 'ai_ml',
        'llm': 'ai_ml',
        'chatbot': 'bot',
        'automation': 'bot',
        'scraper': 'bot',
        'data': 'data_viz',
        'dashboard': 'data_viz',
        'analytics': 'data_viz',
        'cli': 'cli_tool',
        'script': 'cli_tool',
        'tool': 'cli_tool',
        'utility': 'cli_tool',
        'package': 'library',
        'sdk': 'library',
        'browser_extension': 'extension',
        'chrome_extension': 'extension',
        'firefox_extension': 'extension',
        'addon': 'extension',
        'add_on': 'extension',
        'boilerplate': 'template',
        'starter': 'template',
        'ui_kit': 'template',
        'theme': 'template',
    }
    return aliases.get(v, DEFAULT_KIND)

def kind_meta(value):
    """Display metadata for a kind — never raises, never returns None."""
    return KIND_BY_VALUE.get(coerce_kind(value), KIND_BY_VALUE[DEFAULT_KIND])

def kind_label(value):
    return kind_meta(value)['label']

def kind_icon(value):
    return kind_meta(value)['icon']

def preview_mode_for(kind, has_html, has_zip, static_runnable=False):
    """What this specific upload can honestly offer.
    """
    if has_html:
        return 'snippet'
    if has_zip and static_runnable:
        return 'static_zip'
    if has_zip:
        return 'files'
    # Neither: the upload form rejects this, but be defensive.
    return 'files'
