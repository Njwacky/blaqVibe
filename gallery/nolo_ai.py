import logging
import os

import requests

from .prompt_economy import optimize_prompt, should_enable_prefix_cache

logger = logging.getLogger(__name__)

NOLO_SYSTEM_PROMPT = (
    'You are Nolo on BlaqVibes. Answer the question only. '
    'Stay concise, plain text, under 120 words. '
    'If you are unsure, say so. Do not claim to be live when no model key is set.'
)


def _env(name: str) -> str:
    try:
        from django.conf import settings
        val = getattr(settings, name, '') or os.getenv(name, '')
    except Exception:
        val = os.getenv(name, '')
    return (val or '').strip()


def _int_setting(name, default):
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


def configured_ai_backend() -> str:
    """Which live model we will try first. heuristic = no API key."""
    if _env('ANTHROPIC_API_KEY'):
        return 'claude'
    if _env('GEMINI_API_KEY'):
        return 'gemini'
    if _env('GROQ_API_KEY'):
        return 'groq'
    return 'heuristic'


def _max_output_tokens(default=180):
    return max(80, _int_setting('NOLO_OUTPUT_MAX_TOKENS', default))


def _system_prompt():
    return _env('NOLO_SYSTEM_PROMPT') or NOLO_SYSTEM_PROMPT


def get_nolo_ai_answer(prompt, *, system_text=None, budget_chars=None, preserve_code=False, return_meta=False):
    """Return (reply, source) — or (reply, source, meta) when return_meta=True.

    Every backend gets the same token-economy plan: stable system instructions
    first, then the compressed/capped dynamic user payload. `source` is
    claude|gemini|groq|heuristic. No API key → no fake live model.
    """
    sys_text = system_text or _system_prompt()
    chat_budget = budget_chars if budget_chars is not None else _int_setting('NOLO_CHAT_USER_BUDGET_CHARS', 1800)
    plan = optimize_prompt(
        prompt,
        system=sys_text,
        user_budget_chars=chat_budget,
        preserve_code=bool(preserve_code),
    )
    prompt_text = plan['text']

    claude_key = _env('ANTHROPIC_API_KEY')
    if claude_key:
        try:
            text = _claude_answer(claude_key, prompt_text, plan['system'])
            if text:
                return _maybe_meta(text, 'claude', plan, return_meta)
        except Exception as e:
            logger.warning('Claude chat failed: %s', e)
    gemini_key = _env('GEMINI_API_KEY')
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model_kwargs = {'model': _env('GEMINI_MODEL') or 'gemini-1.5-flash'}
            try:
                model_kwargs['system_instruction'] = plan['system']
            except Exception:
                pass
            model = genai.GenerativeModel(**model_kwargs)
            resp = model.generate_content(
                prompt_text,
                generation_config={
                    'temperature': 0.4,
                    'max_output_tokens': _max_output_tokens(240),
                },
            )
            text = getattr(resp, 'text', '') or str(resp)
            if text:
                return _maybe_meta(text, 'gemini', plan, return_meta)
        except Exception as e:
            logger.warning('Gemini chat failed: %s', e)
    groq_key = _env('GROQ_API_KEY')
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            messages = []
            if plan['system']:
                messages.append({'role': 'system', 'content': plan['system']})
            messages.append({'role': 'user', 'content': prompt_text})
            resp = client.chat.completions.create(
                model=_env('GROQ_MODEL') or 'llama-3.1-8b-instant',
                messages=messages,
                max_tokens=_max_output_tokens(240),
                temperature=0.4,
            )
            text = resp.choices[0].message.content
            if text:
                return _maybe_meta(text, 'groq', plan, return_meta)
        except Exception as e:
            logger.warning('Groq chat failed: %s', e)

    reply = _heuristic_fallback(prompt)
    return _maybe_meta(reply, 'heuristic', plan, return_meta)


def _maybe_meta(reply, source, plan, return_meta):
    if not return_meta:
        return reply, source
    meta = {
        'prompt': {
            'input_tokens': plan['after_tokens'],
            'input_tokens_before': plan['before_tokens'],
            'saved_tokens': plan['saved_tokens'],
            'saved_percent': plan['saved_percent'],
        },
        'structure': {
            'ordering': plan['ordering'],
            'cacheable_prefix': plan['cacheable'],
            'truncated': plan['truncated'],
        },
        'warnings': plan['warnings'],
    }
    return reply, source, meta


def _claude_answer(api_key: str, prompt_text: str, system_text: str) -> str:
    body = {
        'model': _env('ANTHROPIC_MODEL') or 'claude-3-5-haiku-latest',
        'max_tokens': _max_output_tokens(240),
        'messages': [{'role': 'user', 'content': prompt_text}],
    }
    if system_text:
        if should_enable_prefix_cache(system_text):
            body['system'] = [
                {
                    'type': 'text',
                    'text': system_text,
                    'cache_control': {'type': 'ephemeral'},
                }
            ]
        else:
            body['system'] = system_text
    r = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        json=body,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    parts = []
    for block in data.get('content') or []:
        if isinstance(block, dict) and block.get('type') == 'text':
            parts.append(block.get('text') or '')
    return ''.join(parts).strip()


def _heuristic_fallback(prompt):
    prompt = (prompt or '').lower()
    if 'preview' in prompt or 'docker' in prompt or 'live zip' in prompt:
        return (
            'Preview files is an in-app page, not Docker. Snippets open in a sandboxed iframe. '
            'ZIP apps show the file list and README. Download the ZIP after a star trade to run it on your machine.'
        )
    if 'star' in prompt or 'trade' in prompt or 'download' in prompt:
        return (
            'Stars are the working money path. New accounts start with 5 ★. '
            'Trade the vibe’s star cost to unlock the ZIP. Card checkout only works if PAYSTACK_SECRET_KEY is set.'
        )
    if 'new apps' in prompt or 'new app' in prompt or 'latest' in prompt:
        return 'Check the latest published vibes section on this page for the newest apps and templates. You can also filter by category to find fresh content.'
    if 'template' in prompt or 'react' in prompt or 'vue' in prompt or 'html' in prompt:
        return 'Look for published vibes with a tech stack that matches your needs. React and Vue templates are usually tagged with those frameworks, while plain HTML/CSS/JS apps are best for quick remixing.'
    if 'compare' in prompt or 'easy' in prompt or 'fork' in prompt:
        return 'Use the Nolo compare tool on an app page to compare features, file count, and tech stack. The easiest vibes to fork are the ones with few files and a clear README.'
    return 'Ask about preview files, stars trades, new apps, or which vibe is easiest to fork. This built-in helper is not a live Claude/Gemini model — set ANTHROPIC_API_KEY, GEMINI_API_KEY, or GROQ_API_KEY to use one.'
