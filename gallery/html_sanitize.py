import bleach, logging
logger = logging.getLogger(__name__)

# Allowlist for html_code — why not |safe raw? Raw allows <img onerror=fetch(cookie)>
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
        # bleach will strip disallowed tags/attrs, including on* and javascript:
        clean = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=ALLOWED_PROTOCOLS, strip=True)
        # Extra: remove any remaining on* attributes that bleach might miss via regex (defense in depth)
        import re
        clean = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', clean, flags=re.I)
        clean = re.sub(r'javascript\s*:', '', clean, flags=re.I)
        return clean
    except Exception as e:
        logger.exception(f"sanitize_html_code crush: {e}")
        return ""  # crush silently, return safe empty
