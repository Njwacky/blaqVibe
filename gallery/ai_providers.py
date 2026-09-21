"""Small provider adapters shared by the background AI features.

Preference order (see blaqvibes/settings.py): OpenRouter → OpenAI → Claude →
Gemini → Groq → built-in heuristic. The first two speak the same
chat-completions protocol, so one adapter (`openai_compatible_text`) covers
both; `preferred_backend()` tells callers which one is live so they can label
the answer honestly.
"""

import os

import requests

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
OPENAI_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_OPENROUTER_MODEL = 'openai/gpt-4o-mini'
DEFAULT_OPENAI_MODEL = 'gpt-4o-mini'


def _setting(name, default=''):
    try:
        from django.conf import settings
        value = getattr(settings, name, default)
    except Exception:
        value = os.getenv(name, default)
    return (value or default).strip() if isinstance(value, str) else value


def openai_compatible_config():
    """Resolve the first-preference chat provider, or None when no key is set.

    Returns ``{'source': 'openrouter' | 'openai', 'key', 'base_url', 'model'}``.
    An OpenRouter key stored under an OpenAI-style name (``sk-or-…``) is routed
    to OpenRouter, so the Render variable can be called whatever it is called.
    """
    key = _setting('OPENROUTER_API_KEY')
    if key:
        return {
            'source': 'openrouter',
            'key': key,
            'base_url': OPENROUTER_BASE_URL,
            'model': _setting('OPENROUTER_MODEL', DEFAULT_OPENROUTER_MODEL),
        }
    key = _setting('OPENAI_API_KEY')
    if key:
        base_url = (_setting('OPENAI_BASE_URL') or OPENAI_BASE_URL).rstrip('/')
        if key.startswith('sk-or-') or 'openrouter.ai' in base_url:
            return {
                'source': 'openrouter',
                'key': key,
                'base_url': OPENROUTER_BASE_URL,
                'model': _setting('OPENROUTER_MODEL', DEFAULT_OPENROUTER_MODEL),
            }
        return {
            'source': 'openai',
            'key': key,
            'base_url': base_url,
            'model': _setting('OPENAI_MODEL', DEFAULT_OPENAI_MODEL),
        }
    return None


def preferred_backend():
    """'openrouter' | 'openai' | '' — the label for the first-preference provider."""
    config = openai_compatible_config()
    return config['source'] if config else ''


def _message_text(message):
    """Chat-completions ``content`` is a string, but some gateways return parts."""
    content = (message or {}).get('content')
    if isinstance(content, list):
        return ''.join(
            part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') in (None, 'text')
        )
    return content or ''


def openai_compatible_text(prompt, *, temperature=0.4, max_output_tokens=240, system=None):
    """Generate text through OpenRouter or OpenAI (whichever is configured).

    Same contract as the other adapters: '' when no key is set, raises on a
    transport/API error so the caller can log it and fall through.
    """
    config = openai_compatible_config()
    if not config:
        return ''
    from .ai_safety import redact_for_ai, user_content_prompt

    messages = ([{'role': 'system', 'content': system}] if system else [])
    messages.append({'role': 'user', 'content': user_content_prompt(prompt)})
    headers = {
        'Authorization': f"Bearer {config['key']}",
        'Content-Type': 'application/json',
    }
    if config['source'] == 'openrouter':
        # Attribution headers OpenRouter asks apps to send (shown on the activity page).
        headers['HTTP-Referer'] = _setting('SITE_URL', 'https://blaqvibes.co.za')
        headers['X-Title'] = 'BlaqVibes Nolo'
    response = requests.post(
        f"{config['base_url']}/chat/completions",
        headers=headers,
        json={
            'model': config['model'],
            'messages': messages,
            'max_tokens': max_output_tokens,
            'temperature': temperature,
        },
        timeout=20,
    )
    response.raise_for_status()
    choices = response.json().get('choices') or []
    text = _message_text(choices[0].get('message')) if choices else ''
    return redact_for_ai(text.strip())


def gemini_text(prompt, *, temperature=0.4, max_output_tokens=240, system=None):
    """Generate text through Gemini's current REST API."""
    key = _setting('GEMINI_API_KEY')
    if not key:
        return ''
    model = _setting('GEMINI_MODEL', 'gemini-2.5-flash')
    from .ai_safety import redact_for_ai, user_content_prompt
    body = {
        'contents': [{'role': 'user', 'parts': [{'text': user_content_prompt(prompt)}]}],
        'generationConfig': {
            'temperature': temperature,
            'maxOutputTokens': max_output_tokens,
        },
    }
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    response = requests.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        headers={'x-goog-api-key': key},
        json=body,
        timeout=20,
    )
    response.raise_for_status()
    candidates = response.json().get('candidates') or []
    parts = (candidates[0].get('content', {}).get('parts') or []) if candidates else []
    return redact_for_ai(
        ''.join(part.get('text', '') for part in parts if isinstance(part, dict)).strip()
    )


def groq_text(prompt, *, temperature=0.4, max_output_tokens=240, system=None):
    """Generate text through Groq using the configured model."""
    key = _setting('GROQ_API_KEY')
    if not key:
        return ''
    from groq import Groq
    from .ai_safety import redact_for_ai, user_content_prompt

    messages = ([{'role': 'system', 'content': system}] if system else [])
    messages.append({'role': 'user', 'content': user_content_prompt(prompt)})
    response = Groq(api_key=key).chat.completions.create(
        model=_setting('GROQ_MODEL', 'llama-3.1-8b-instant'),
        messages=messages,
        max_tokens=max_output_tokens,
        temperature=temperature,
    )
    return redact_for_ai((response.choices[0].message.content or '').strip())
