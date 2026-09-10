"""Redaction and instruction-boundary helpers for hosted AI prompts."""

import re


_SECRET_PATTERNS = (
    r'(api[_ -]?key|secret|password|passwd|token|authorization|bearer)'
    r'(\s*[:=]\s*|\s+)[^\s,;]+',
    r'\b(?:sk|pk)_[A-Za-z0-9_-]{12,}\b',
    r'\b(?:ghp|github_pat|glpat)-[A-Za-z0-9_-]{12,}\b',
    r'\bAIza[0-9A-Za-z_-]{20,}\b',
    r'\bhttps?://[^\s/]+:[^@\s]+@[^\s]+',
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
