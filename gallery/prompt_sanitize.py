import bleach, re, logging
logger = logging.getLogger(__name__)

# 5 Whys Prompt Fields:
# 1. Many prompt fields (ai_prompt, readme, comment, search q) are user text → XSS + prompt injection.
# 2. Why sanitize? Prompt shown in detail page + fed to Nolo future LLM → injection can steal.
# 3. Why separate from readme? Prompt is free-form, may contain "ignore previous instructions".
# 4. Why try/except? Malicious prompt with 100k chars + weird unicode should not crash app — fail silently, log, return safe.

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
        # Truncate silently — don't throw
        if len(text) > PROMPT_MAX_LEN:
            text = text[:PROMPT_MAX_LEN]
            logger.warning(f"Prompt truncated to {PROMPT_MAX_LEN}")
        # Injection check — log, don't crash, strip pattern
        for pat in PROMPT_INJECTION_PATTERNS:
            if pat.search(text):
                logger.warning(f"Prompt injection pattern detected: {pat.pattern[:30]}")
                text = pat.sub('[filtered]', text)
        # Strip all HTML — prompts are plain text
        clean = bleach.clean(text, tags=[], strip=True)
        return clean.strip()
    except Exception as e:
        # Crush silently — log, return safe empty
        logger.exception(f"sanitize_prompt failed: {e}")
        return ""

def is_prompt_safe(text: str) -> bool:
    try:
        return not any(p.search(text or '') for p in PROMPT_INJECTION_PATTERNS)
    except:
        return False
