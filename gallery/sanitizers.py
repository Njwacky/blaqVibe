import markdown
import bleach
try:
    import nh3
    HAS_NH3 = True
except ImportError:
    HAS_NH3 = False

ALLOWED_TAGS = ['p','br','h1','h2','h3','h4','h5','h6','a','ul','ol','li','code','pre','blockquote','strong','em','hr','table','thead','tbody','tr','th','td','span','div']
ALLOWED_ATTRS = {'a': ['href','title'], 'code': ['class'], 'span': ['class'], 'div': ['class']}
ALLOWED_PROTOCOLS = ['http','https','mailto']

def _clean(html: str) -> str:
    if HAS_NH3:
        return nh3.clean(html, tags=set(ALLOWED_TAGS), attributes={k: set(v) for k,v in ALLOWED_ATTRS.items()}, url_schemes=set(ALLOWED_PROTOCOLS))
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=ALLOWED_PROTOCOLS, strip=True)

def render_readme(md_text: str) -> str:
    html = markdown.markdown(md_text or "", extensions=['fenced_code','codehilite','tables'])
    return _clean(html)

def render_markdown_inline(md_text: str) -> str:
    # For comments — allow limited markdown
    html = markdown.markdown(md_text or "", extensions=['fenced_code'])
    return _clean(html)
