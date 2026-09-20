"""Redaction and instruction-boundary helpers for hosted AI prompts."""

import re


_SECRET_PATTERNS = (
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----',
    r'(api[_ -]?key|secret|password|passwd|token|authorization|bearer)'
    r'(\s*[:=]\s*|\s+)[^\s,;]+',
    # Provider key shapes: OpenRouter (sk-or-v1-), OpenAI (sk-/sk-proj-),
    # Anthropic (sk-ant-), Groq (gsk_), Paystack/Stripe (sk_live_/sk_test_),
    # GitHub/GitLab, Google, AWS, Slack, JWTs.
    r'\bsk-(?:or-v1-|ant-|proj-)?[A-Za-z0-9_-]{20,}\b',
    r'\bgsk_[A-Za-z0-9]{20,}\b',
    r'\b(?:sk|pk|rk|whsec)_[A-Za-z0-9_-]{12,}\b',
    r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b',
    r'\b(?:ghp|github_pat|glpat)-[A-Za-z0-9_-]{12,}\b',
    r'\bgithub_pat_[A-Za-z0-9_]{20,}\b',
    r'\bAIza[0-9A-Za-z_-]{20,}\b',
    r'\bAKIA[0-9A-Z]{16}\b',
    r'\bxox[abprs]-[A-Za-z0-9-]{10,}\b',
    r'\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b',
    r'\bhttps?://[^\s/]+:[^@\s]+@[^\s]+',
    r'\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@[^\s]+',
)
_SECRET_RE = re.compile('|'.join(f'(?:{pattern})' for pattern in _SECRET_PATTERNS), re.I)


def redact_for_ai(value, limit=8000):
    text = str(value or '')
    text = _SECRET_RE.sub('[REDACTED]', text)
    text = re.sub(r'(?i)(?:/home/|/users/|[A-Z]:\\Users\\)[^\s"\']+', '[PRIVATE_PATH]', text)
    return text[:limit]


def user_content_prompt(value, limit=8000):
    """Fence untrusted content so it cannot become provider instructions."""
    return '<untrusted_user_content>\n' + redact_for_ai(value, limit) + '\n</untrusted_user_content>'
