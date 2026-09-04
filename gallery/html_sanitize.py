import bleach, logging
logger = logging.getLogger(__name__)

ALLOWED_TAGS = ['a','abbr','acronym','b','blockquote','code','div','em','h1','h2','h3','h4','h5','h6','i','img','li','ol','p','pre','span','strong','ul','br','hr','section','nav','header','footer','main','aside','article','table','thead','tbody','tr','th','td']
ALLOWED_ATTRS = {
    'a': ['href','title'],
    'img': ['src','alt','width','height'],
    'div': ['class'],
    'span': ['class'],
    'p': ['class'],
    'h1': ['class'], 'h2': ['class'], 'h3': ['class'],
    'section': ['class'], 'nav': ['class'],
}
ALLOWED_PROTOCOLS = ['http','https','mailto','data']

def sanitize_html_code(html: str) -> str:
    """Backend only, crush silently — strips on*, javascript:, event handlers."""
    try:
        if not html:
            return ""
        clean = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=ALLOWED_PROTOCOLS, strip=True)
        import re
        clean = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', clean, flags=re.I)
        clean = re.sub(r'javascript\s*:', '', clean, flags=re.I)
        return clean
    except Exception as e:
        logger.exception(f"sanitize_html_code crush: {e}")
        return ""
