"""Real token-saving prompt engine for BlaqVibes' AI helpers.

This module is the single place where Nolo's prompts are made cheap. It does
not pretend to count tokens exactly (that needs a model-specific BPE
tokenizer), but it does the structural work that actually saves tokens on 2026
frontier models: a short, stable system prefix first; the dynamic user payload
last, compressed and budget-capped; provider cache hints only when the prefix
is large enough for them to be honoured; and an honest savings report back to
the caller.

5 Whys — why a dedicated prompt-economy layer instead of inline tweaks?

Why 1 — Why a separate module instead of editing the prompt inside each model call?
    - The three backends (Claude / Gemini / Groq) all build prompts today; a
      tweak done in one place silently misses the other two and the savings
      disappear on the next model switch.
    - The prompt rules need tests. A pure module with no network, no requests,
      and no Django import can be unit-tested in milliseconds from a shell or
      CI; the view layer cannot.
    - The savings need to be observable. Returning `meta` with before/after
      estimates lets the product show honest cost behaviour instead of
      pretending a magic "cheap mode" exists.
    - The rules can drift. A central `optimize_prompt()` contract means the
      code, README, and classification helpers can be upgraded in one place
      without re-testing every endpoint.

Why 2 — Why "system first, dynamic last" instead of one big content blob?
    - Provider prompt caching bills the repeated prefix at up to ~90% off when
      the prefix is stable and long enough; the dynamic user text is what must
    travel in the "cache miss" tail.
    - Models are sensitive to position: the most important facts belong at the
      beginning or the end. Putting the user question last means the model
      reads it immediately before generating, which is where it belongs.
    - Static instructions cannot be "cached" if they are interleaved with the
      user data, because a single changing byte invalidates the whole prefix
      block on Anthropic.
    - It also makes the safety story simpler: the fixed instructions are under
      our control, while the user text stays clearly separated as untrusted.

Why 3 — Why compress whitespace, dedupe lines, and cap budgets deterministically?
    - Wall-of-text whitespace is real token waste: three blank lines and lots
      of trailing spaces are tokens the model has to read.
    - Duplicate prose lines are nearly always an accident in a long prompt,
      and removing them is lossless for prose; we deliberately skip that for
      code, where two identical lines can be semantically meaningful.
    - Budget caps are the lever we can guarantee without a tokenizer: 1,600
      chars of prose is far cheaper than 36,000 chars of pasted code, and the
      cap is visible in `meta.warnings` rather than silently changing output.
    - If we cannot safely justify a more aggressive rewrite (for example,
      stripping arbitrary "filler" words from user code), we take the other
      approach: keep the bytes, cap the budget, and say so. Trying to be
      clever where we cannot be correct is a shortcut, not an optimisation.

Why 4 — Why estimate tokens ourselves instead of asking the provider?
    - No extra dependency: the repo deliberately avoids a large tokenizer
      package, and the estimate only needs to be reasonable, not exact.
    - Costs in production are priced per token, so an order-of-magnitude
      estimate plus the character budget is enough to make the right scoping
      decision before a network call.
    - It must work offline and in tests, where no API key is present; if an
      exact count is needed later, the module can be swapped behind the same
      function signature.
    - It keeps the function total: same input, same estimate, no hidden state.

Why 5 — Why return `meta` and keep the old two-value API?
    - Existing callers (nolo_assist, tests) already behave correctly with
      `(reply, source)`; changing the default contract would break them.
    - The view that wants economics can opt in with `return_meta=True` and
      expose `{input_tokens, saved_tokens, ...}` without disturbing the rest.
    - A structured report is testable: `optimize_prompt()` can be asserted to
      have `saved_tokens >= 0`, `truncated` matches the budget, and code mode
      preserves indentation.
    - If a point above turns out to be wrong in practice, the same pure
      function is easy to swap: the choices are isolated behind one API, not
      scattered across request bodies.

---
"""

import logging
import os
import re
from math import ceil

logger = logging.getLogger(__name__)

__all__ = [
    'DEFAULT_USER_BUDGET_CHARS',
    'DEFAULT_SYSTEM_BUDGET_CHARS',
    'estimate_tokens',
    'optimize_prompt',
    'should_enable_prefix_cache',
]

DEFAULT_USER_BUDGET_CHARS = 1800
DEFAULT_SYSTEM_BUDGET_CHARS = 900
MIN_BUDGET_CHARS = 240
LONG_INPUT_ALERT_CHARS = 9000

_CHARS_PER_TOKEN = 4.0
_CODE_PUNCT_WEIGHT = 0.10

_MAX_BLANK_LINES = 2


def _int_env(name, default):
    try:
        from django.conf import settings
        raw = getattr(settings, name, '') or os.getenv(name, '')
    except Exception:
        raw = os.getenv(name, '')
    raw = (raw or '').strip()
    if not raw:
        return default
    try:
        return max(int(raw), MIN_BUDGET_CHARS)
    except (TypeError, ValueError):
        logger.warning('Invalid integer setting %s=%r, using %s', name, raw, default)
        return default


def estimate_tokens(text: str) -> int:
    """Approximate provider token count for plain text.

    Deliberately a heuristic (roughly 4 characters per token, plus a small
    code punctuation penalty). It is used for budgeting, never as an invoice.
    """
    if not text:
        return 0
    plain = str(text)
    if len(plain) < LONG_INPUT_ALERT_CHARS:
        return max(1, ceil(len(plain) / _CHARS_PER_TOKEN))
    # Long input: punishment for punctuation is meaningful because code and
    # JSON have a much higher token-per-char ratio than prose.
    punct = len(re.findall(r'[\W_]+', plain))
    base = ceil(len(plain) / _CHARS_PER_TOKEN)
    return max(1, int(base * (1.0 + _CODE_PUNCT_WEIGHT * min(1.0, punct / max(1, len(plain))))))


def _strip_crlf(text: str) -> str:
    return str(text).replace('\r\n', '\n').replace('\r', '\n')


def _trim_trailing_ws(text: str) -> str:
    lines = text.split('\n')
    return '\n'.join(line.rstrip() for line in lines)


def _collapse_blank_lines(text: str) -> str:
    out = []
    blank_run = 0
    for line in text.split('\n'):
        if not line.strip():
            blank_run += 1
            if blank_run > _MAX_BLANK_LINES:
                continue
            out.append('')
        else:
            blank_run = 0
            out.append(line)
    return '\n'.join(out).strip()


def _compact_prose_spaces(text: str) -> str:
    # Collapse runs of spaces to one. Do NOT touch indentation/newlines so we
    # stay safe for code; this only runs in non-code mode on prose.
    return re.sub(r'(?<!\n)\s{2,}(?!\n)', ' ', text)


def dedupe_lines(text: str, *, preserve_code: bool = False) -> str:
    """Drop exact duplicate lines while preserving order.

    Skipped entirely when `preserve_code` is true because repeated lines in
    code (two same function calls, two same SQL strings) must be kept.
    """
    if preserve_code or not text:
        return text
    seen = set()
    out = []
    for line in text.split('\n'):
        key = line.strip()
        if key not in seen:
            seen.add(key)
            out.append(line)
    return '\n'.join(out)


def _truncate_to_budget(text: str, max_chars: int, *, preserve_code: bool) -> str:
    if len(text) <= max_chars:
        return text
    if preserve_code:
        cut = text[:max_chars]
        # For code we cut on a line boundary, never inside a token.
        if '\n' in cut:
            cut = cut.rsplit('\n', 1)[0]
    else:
        cut = text[:max_chars]
        space = cut.rfind(' ')
        if space > max_chars * 0.6:
            cut = cut[:space]
    return cut.strip()


def should_enable_prefix_cache(system_text: str, *, min_tokens: int = None) -> bool:
    """Whether the stable system prefix is big enough for a provider cache hint.

    Anthropic requires a minimum (default 1024 tokens) for the cache block to
    be honoured. Sending the hint anyway is not a secret cost bug, but it
    risks a provider error on small prompts, so we only emit it when the
    prefix is large enough to matter.
    """
    raw = _env('NOLO_CACHE_MIN_TOKENS', '')
    if raw.strip() == '0':
        return False
    threshold = _int_env('NOLO_CACHE_MIN_TOKENS', min_tokens or 1024)
    return estimate_tokens(system_text) >= threshold


def _env(name, default=''):
    try:
        from django.conf import settings
        val = getattr(settings, name, '') or os.getenv(name, default)
    except Exception:
        val = os.getenv(name, default)
    return (val or '').strip()


def _clamp_budget(value, default):
    try:
        val = int(value)
        if val <= 0:
            return default
        return max(MIN_BUDGET_CHARS, val)
    except (TypeError, ValueError):
        return default


def optimize_prompt(
    text,
    *,
    system='',
    user_budget_chars=None,
    system_budget_chars=None,
    preserve_code=False,
    dedupe=True,
):
    """Return the cheapest truthful prompt plus a real savings report.

    This is the drop-in "best function" for the Nolo helpers. It never trips
    on user text: a bad input degrades to a safe, capped prompt instead of an
    exception, and every destructive choice is reported in `meta`.
    """
    try:
        user_budget_chars = _clamp_budget(user_budget_chars, DEFAULT_USER_BUDGET_CHARS)
        system_budget_chars = _clamp_budget(system_budget_chars, DEFAULT_SYSTEM_BUDGET_CHARS)
        raw = (text or '')
        raw_system = (system or '')

        # Before numbers are for the input side only (output is controlled
        # separately by max_tokens in the model call).
        before_user_tokens = estimate_tokens(raw)
        before_system_tokens = estimate_tokens(raw_system)

        user_text = _strip_crlf(raw)
        user_text = _trim_trailing_ws(user_text)
        user_text = _collapse_blank_lines(user_text)
        if not preserve_code:
            user_text = _compact_prose_spaces(user_text)
        if dedupe:
            user_text = dedupe_lines(user_text, preserve_code=preserve_code)

        system_text = _strip_crlf(raw_system)
        system_text = _trim_trailing_ws(system_text)
        system_text = _collapse_blank_lines(system_text)
        if not preserve_code:
            system_text = _compact_prose_spaces(system_text)
        if dedupe:
            system_text = dedupe_lines(system_text, preserve_code=preserve_code)

        user_truncated = False
        if len(user_text) > user_budget_chars:
            user_text = _truncate_to_budget(user_text, user_budget_chars, preserve_code=preserve_code)
            user_truncated = True
        system_truncated = False
        if len(system_text) > system_budget_chars:
            system_text = _truncate_to_budget(system_text, system_budget_chars, preserve_code=False)
            system_truncated = True

        after_user_tokens = estimate_tokens(user_text)
        after_system_tokens = estimate_tokens(system_text)
        before_total = before_user_tokens + before_system_tokens
        after_total = after_user_tokens + after_system_tokens
        saved = max(0, before_total - after_total)
        saved_percent = round((saved / before_total) * 100) if before_total else 0

        warnings = []
        if user_truncated:
            warnings.append(
                f'User context was capped to {user_budget_chars} characters; '
                'the model answered from the kept segment.'
            )
        if system_truncated:
            warnings.append(
                f'System instructions were capped to {system_budget_chars} characters.'
            )
        if preserve_code and user_truncated:
            warnings.append('The code was cut on a line boundary, never mid-token.')

        cacheable = should_enable_prefix_cache(system_text)

        return {
            'text': user_text,
            'system': system_text,
            'before_tokens': before_total,
            'after_tokens': after_total,
            'saved_tokens': saved,
            'saved_percent': saved_percent,
            'user_tokens': after_user_tokens,
            'system_tokens': after_system_tokens,
            'user_chars': len(user_text),
            'system_chars': len(system_text),
            'truncated': user_truncated or system_truncated,
            'user_truncated': user_truncated,
            'system_truncated': system_truncated,
            'cacheable': cacheable,
            'ordering': 'system-first-dynamic-last',
            'compression': 'blank-lines/trailing-ws/dedupe/budget',
            'preserve_code': bool(preserve_code),
            'warnings': warnings,
        }
    except Exception as exc:
        # A prompt manager must never take the chat service down. The other
        # approach, when a transformation cannot be explained safely, is to
        # return the original bytes with a warning and let the model call cap
        # its own output.
        logger.exception('optimize_prompt failed: %s', exc)
        raw = text or ''
        return {
            'text': raw,
            'system': system or '',
            'before_tokens': estimate_tokens(raw),
            'after_tokens': estimate_tokens(raw),
            'saved_tokens': 0,
            'saved_percent': 0,
            'user_tokens': estimate_tokens(raw),
            'system_tokens': estimate_tokens(system or ''),
            'user_chars': len(raw),
            'system_chars': len(system or ''),
            'truncated': False,
            'user_truncated': False,
            'system_truncated': False,
            'cacheable': False,
            'ordering': 'fallback-unaltered',
            'compression': 'none',
            'preserve_code': bool(preserve_code),
            'warnings': ['Prompt economy was unable to safely transform this text; it was left unaltered.'],
        }
