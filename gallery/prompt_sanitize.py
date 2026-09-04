import bleach, re, logging
logger = logging.getLogger(__name__)


PROMPT_MAX_LEN = 5000
PROMPT_INJECTION_PATTERNS = [
    re.compile(r'ignore\s+previous\s+instructions', re.I),
    re.compile(r'system\s*:\s*', re.I),
    re.compile(r'jailbreak', re.I),
    re.compile(r'<script', re.I),
]

def sanitize_prompt(text: str) -> str:
    """Backend only, no JS. Strips HTML, limits len, flags injection — crash silently."""
    try:
        if not text:
            return ""
        if len(text) > PROMPT_MAX_LEN:
            text = text[:PROMPT_MAX_LEN]
            logger.warning(f"Prompt truncated to {PROMPT_MAX_LEN}")
        for pat in PROMPT_INJECTION_PATTERNS:
            if pat.search(text):
                logger.warning(f"Prompt injection pattern detected: {pat.pattern[:30]}")
                text = pat.sub('[filtered]', text)
        clean = bleach.clean(text, tags=[], strip=True)
        return clean.strip()
    except Exception as e:
        logger.exception(f"sanitize_prompt failed: {e}")
        return ""

def is_prompt_safe(text: str) -> bool:
    try:
        return not any(p.search(text or '') for p in PROMPT_INJECTION_PATTERNS)
    except Exception:
        return False
