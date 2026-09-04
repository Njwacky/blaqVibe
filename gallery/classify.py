"""Program-kind classification: heuristic floor + selective LLM lift.
Public entry point: `classify_project(project)`. It is called from the
upload pipeline (after the scan, before publish) and from `edit_vibe`.
"""
import json
import logging
import os
import re

from django.core.cache import cache

from .kind_detect import detect_kind
from .taxonomy import DEFAULT_KIND, KIND_VALUES, coerce_kind, preview_mode_for

logger = logging.getLogger(__name__)

# Below this heuristic confidence the row is worth an LLM call.
LLM_CONFIDENCE_FLOOR = 0.55
# Hard ceiling on LLM classification calls per minute, process-wide.
LLM_CALLS_PER_MINUTE = 30
_BUCKET_KEY = 'blaqvibes:kind-llm-bucket'

def _setting(name, default):
    try:
        from django.conf import settings
        return getattr(settings, name, default)
    except Exception:
        return default

def _env(name):
    try:
        from django.conf import settings
        val = getattr(settings, name, '') or os.getenv(name, '')
    except Exception:
        val = os.getenv(name, '')
    return (val or '').strip()

def llm_available():
    """True when some provider key is configured."""
    return bool(_env('ANTHROPIC_API_KEY') or _env('GEMINI_API_KEY') or _env('GROQ_API_KEY'))

def needs_llm(heuristic):
    """Only ambiguous rows are worth a call."""
    if not heuristic:
        return True
    if heuristic.get('kind') == DEFAULT_KIND:
        return True
    return float(heuristic.get('confidence') or 0) < _setting(
        'KIND_LLM_CONFIDENCE_FLOOR', LLM_CONFIDENCE_FLOOR
    )

def _take_budget():
    """Per-minute token bucket. Returns True if this call may proceed.

    Why cache and not a DB counter? This is throttling, not accounting —
    losing the counter on a cache flush costs at most one extra minute of
    calls, while a DB row would put a write on every publish.

    Why a per-minute key instead of a decrementing counter? `cache.incr`
    on a key that expires at the end of the minute is atomic on Redis and
    self-cleaning; no reset task, no drift.
    """
    limit = int(_setting('KIND_LLM_CALLS_PER_MINUTE', LLM_CALLS_PER_MINUTE))
    if limit <= 0:
        return False
    try:
        import time
        key = f'{_BUCKET_KEY}:{int(time.time() // 60)}'
        added = cache.add(key, 1, 120)
        if added:
            return True
        try:
            used = cache.incr(key)
        except ValueError:
            # Key expired between add() and incr() — start a fresh window.
            cache.set(key, 1, 120)
            return True
        return used <= limit
    except Exception:
        # A broken cache must not silently unlock unlimited spend.
        logger.warning('kind LLM budget check failed — skipping LLM')
        return False

_PROMPT = """You label uploaded software projects for a code-sharing site.

Reply with ONLY a JSON object, no prose:
{{"kind": "<one of: {kinds}>", "confidence": 0.0-1.0, "appeal": 0-100, "why": "<max 12 words>"}}

"kind" is what the program IS. "appeal" is how interesting it looks to a
casual browser (a playable game or a polished tool scores high; an empty
boilerplate scores low). Judge only from the facts below.

Title: {title}
One-liner: {desc}
Declared stack: {stack}
Languages: {langs}
File count: {count}
Notable files: {files}
README extract:
{readme}
"""

def _build_prompt(project):
    try:
        from .kind_detect import _shallow_paths
        files = _shallow_paths(project)[:40]
    except Exception:
        files = []
    return _PROMPT.format(
        kinds=', '.join(KIND_VALUES),
        title=(getattr(project, 'title', '') or '')[:120],
        desc=(getattr(project, 'short_description', '') or '')[:200],
        stack=(getattr(project, 'tech_stack', '') or '')[:120],
        langs=', '.join((getattr(project, 'language_stats', None) or {}).keys())[:120],
        count=getattr(project, 'file_count', 0) or 0,
        files=', '.join(files)[:600] or 'none',
        readme=(getattr(project, 'readme', '') or '')[:1200],
    )

def _parse_llm_json(text):
    if not text:
        return None
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    kind = coerce_kind(data.get('kind'))
    try:
        confidence = float(data.get('confidence', 0.6))
    except Exception:
        confidence = 0.6
    try:
        appeal = float(data.get('appeal', 50))
    except Exception:
        appeal = 50.0
    why = str(data.get('why') or '')[:80]
    return {
        'kind': kind,
        'confidence': max(0.0, min(1.0, confidence)),
        'appeal': max(0.0, min(100.0, appeal)),
        'why': why,
    }

def _call_claude(prompt):
    import requests
    key = _env('ANTHROPIC_API_KEY')
    if not key:
        return None
    r = requests.post(
        'https://api.anthropic.com/v1/messages',
        headers={'x-api-key': key, 'anthropic-version': '2023-06-01',
                 'content-type': 'application/json'},
        json={
            'model': _env('ANTHROPIC_MODEL') or 'claude-3-5-haiku-latest',
            'max_tokens': 200,
            'messages': [{'role': 'user', 'content': prompt}],
        },
        timeout=15,
    )
    r.raise_for_status()
    parts = [b.get('text', '') for b in (r.json().get('content') or [])
             if isinstance(b, dict) and b.get('type') == 'text']
    return _parse_llm_json(''.join(parts))

def _call_gemini(prompt):
    key = _env('GEMINI_API_KEY')
    if not key:
        return None
    import google.generativeai as genai
    genai.configure(api_key=key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    resp = model.generate_content(
        prompt, generation_config={'temperature': 0.1, 'max_output_tokens': 200}
    )
    return _parse_llm_json(getattr(resp, 'text', '') or '')

def _call_groq(prompt):
    key = _env('GROQ_API_KEY')
    if not key:
        return None
    from groq import Groq
    client = Groq(api_key=key)
    resp = client.chat.completions.create(
        model='llama-3.1-8b-instant',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=200,
        temperature=0.1,
    )
    return _parse_llm_json(resp.choices[0].message.content or '')

_PROVIDERS = (('claude', _call_claude), ('gemini', _call_gemini), ('groq', _call_groq))

def llm_classify(project):
    """One LLM opinion, or None. Never raises."""
    if not llm_available():
        return None
    if not _take_budget():
        logger.info('kind LLM budget exhausted this minute — heuristic only')
        return None
    prompt = _build_prompt(project)
    for name, fn in _PROVIDERS:
        try:
            out = fn(prompt)
        except Exception as e:
            logger.warning('kind LLM %s failed: %s', name, e)
            continue
        if out:
            out['source'] = name
            return out
    return None

def classify_project(project, allow_llm=True, save=True):
    """Decide kind + preview mode for a project and (optionally) persist. A
    creator's explicit kind pick beats the model (they know what they built),
    but classification still runs to set capability-derived preview_mode and to
    store badge evidence (see kind_detect). `save=False` serves tests and the
    publish preview.
    """
    heuristic = detect_kind(project)
    verdict = dict(heuristic)
    verdict['heuristic_kind'] = heuristic.get('kind')

    if allow_llm and needs_llm(heuristic):
        llm = llm_classify(project)
        if llm:
            verdict['kind'] = llm['kind']
            verdict['confidence'] = llm['confidence']
            verdict['source'] = llm['source']
            verdict['llm_appeal'] = llm['appeal']
            if llm.get('why'):
                verdict['evidence'] = [llm['why']] + list(heuristic.get('evidence') or [])[:3]

    creator = coerce_kind(getattr(project, 'creator_kind', '') or '')
    if getattr(project, 'creator_kind', ''):
        if creator != verdict['kind']:
            verdict['evidence'] = ([f'auto-guess was {verdict["kind"]}']
                                   + list(verdict.get('evidence') or [])[:4])
        verdict['kind'] = creator
        verdict['confidence'] = 1.0
        verdict['source'] = 'creator'

    verdict['kind'] = coerce_kind(verdict.get('kind'))

    # Can a ZIP actually run in the sandbox? Only if it is a static site.
    static_runnable = False
    static_entry = ''
    if not (getattr(project, 'html_code', '') or '').strip() and getattr(project, 'zip_file', None):
        try:
            from .runner import detect_static_runnable
            paths = _project_paths(project)
            static_runnable, static_entry = detect_static_runnable(paths)
        except Exception:
            logger.exception('static runnable detect failed for %s', getattr(project, 'slug', '?'))

    verdict['preview_mode'] = preview_mode_for(
        verdict['kind'],
        bool((getattr(project, 'html_code', '') or '').strip()),
        bool(getattr(project, 'zip_file', None)),
        static_runnable=static_runnable,
    )
    verdict['static_entry'] = static_entry if verdict['preview_mode'] == 'static_zip' else ''

    if save:
        try:
            project.kind = verdict['kind']
            project.kind_source = verdict.get('source') or 'heuristic'
            project.kind_confidence = float(verdict.get('confidence') or 0)
            project.kind_evidence = list(verdict.get('evidence') or [])[:5]
            project.preview_mode = verdict['preview_mode']
            project.static_entry = verdict['static_entry']
            project.save(update_fields=[
                'kind', 'kind_source', 'kind_confidence', 'kind_evidence',
                'preview_mode', 'static_entry',
            ])
        except Exception:
            logger.exception('classify_project save failed for %s', getattr(project, 'slug', '?'))
    return verdict

def _project_paths(project):
    """The archive's file paths — from AppFile rows first, ZIP as fallback.
    """
    try:
        rows = list(project.files.values_list('path', flat=True))
        if rows:
            return rows
    except Exception:
        pass
    try:
        if getattr(project, 'zip_file', None):
            from .ziputil import build_tree
            _, file_list = build_tree(project.zip_file)
            return [f['path'] for f in file_list]
    except Exception:
        logger.exception('project paths from zip failed for %s', getattr(project, 'slug', '?'))
    return []
