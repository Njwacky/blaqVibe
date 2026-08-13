import logging
import os

import requests

logger = logging.getLogger(__name__)


def _env(name: str) -> str:
    try:
        from django.conf import settings
        val = getattr(settings, name, '') or os.getenv(name, '')
    except Exception:
        val = os.getenv(name, '')
    return (val or '').strip()


def configured_ai_backend() -> str:
    """Which live model we will try first. heuristic = no API key."""
    if _env('ANTHROPIC_API_KEY'):
        return 'claude'
    if _env('GEMINI_API_KEY'):
        return 'gemini'
    if _env('GROQ_API_KEY'):
        return 'groq'
    return 'heuristic'


def get_nolo_ai_answer(prompt):
    """Return (reply, source). source is claude|gemini|groq|heuristic.

    Claude is used only when ANTHROPIC_API_KEY is set. No key → no fake Claude.
    """
    prompt_text = (
        "Answer this BlaqVibes question in a short helpful way:\n\n"
        f"{prompt}\n\nKeep the answer concise and friendly."
    )
    claude_key = _env('ANTHROPIC_API_KEY')
    if claude_key:
        try:
            text = _claude_answer(claude_key, prompt_text)
            if text:
                return text, 'claude'
        except Exception as e:
            logger.warning('Claude chat failed: %s', e)
    gemini_key = _env('GEMINI_API_KEY')
    if gemini_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            resp = model.generate_content(prompt_text, generation_config={'temperature': 0.4, 'max_output_tokens': 300})
            text = getattr(resp, 'text', '') or str(resp)
            if text:
                return text, 'gemini'
        except Exception as e:
            logger.warning('Gemini chat failed: %s', e)
    groq_key = _env('GROQ_API_KEY')
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model='llama-3.1-8b-instant',
                messages=[{'role': 'user', 'content': prompt_text}],
                max_tokens=300,
                temperature=0.4,
            )
            text = resp.choices[0].message.content
            if text:
                return text, 'groq'
        except Exception as e:
            logger.warning('Groq chat failed: %s', e)
    return _heuristic_fallback(prompt), 'heuristic'


def _claude_answer(api_key: str, prompt_text: str) -> str:
    r = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json',
        },
        json={
            'model': _env('ANTHROPIC_MODEL') or 'claude-3-5-haiku-latest',
            'max_tokens': 300,
            'messages': [{'role': 'user', 'content': prompt_text}],
        },
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
