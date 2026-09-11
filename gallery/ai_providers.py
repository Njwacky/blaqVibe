"""Small provider adapters shared by the background AI features."""

import os

import requests


def _setting(name, default=''):
    try:
        from django.conf import settings
        value = getattr(settings, name, default)
    except Exception:
        value = os.getenv(name, default)
    return (value or default).strip() if isinstance(value, str) else value


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
