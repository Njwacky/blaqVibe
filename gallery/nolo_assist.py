"""Nolo's assistant skills — fix code and write a README.

Two things a new vibe coder actually needs: "why is my code broken?" and
"write me a README". Both work WITHOUT an API key (a real static analyser and
a real structured README generator), and get better WITH one (the analysis is
handed to the model as grounding, never replaced by a guess).

5 Whys — why heuristics first, LLM only as a lift?

1. Why must this work with no key at all? The whole site's honesty rule is
   "never pretend to be a live model" (see nolo_ai). A beginner on a fresh
   deploy with no key must still get a genuinely useful answer, not a stub.
2. Why a real static analyser instead of "set a key for help"? The common
   beginner breakages — an unclosed tag, `getElementByID`, a missing bracket,
   calling the DOM before it exists — are detectable deterministically in
   milliseconds, offline, with no hallucination risk.
3. Why still call the LLM when a key exists? Heuristics catch the frequent
   mistakes; a model explains the unusual one and writes prose. Feeding it the
   heuristic findings grounds it so it fixes the ACTUAL code, not an imagined
   version.
4. Why cap and sanitise the input? Code pasted into a form is user text — it
   goes through the same length cap and prompt-injection scrub as any prompt
   (see prompt_sanitize) before it can reach a model or the page.
5. Why return structured findings, not one blob? The Studio renders each
   finding next to the code; a list the UI can iterate beats a wall of text,
   and tests can assert a specific check fired.
"""
import logging
import re

logger = logging.getLogger(__name__)

MAX_CODE_LEN = 12000


def _clip(text):
    text = text or ''
    return text[:MAX_CODE_LEN]


# --------------------------------------------------------------------------- #
# Skill 1: Fix my code
# --------------------------------------------------------------------------- #

# Void elements never need a closing tag — don't flag them as "unclosed".
_VOID_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
}


def _check_html(html, findings):
    if not html.strip():
        return
    # Unbalanced tags — a classic reason "nothing shows up".
    open_tags = re.findall(r'<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(?<!/)>', html)
    close_tags = re.findall(r'</([a-zA-Z][a-zA-Z0-9]*)\s*>', html)
    from collections import Counter
    opened = Counter(t.lower() for t in open_tags if t.lower() not in _VOID_TAGS)
    closed = Counter(t.lower() for t in close_tags)
    for tag, n in opened.items():
        if n > closed.get(tag, 0):
            findings.append({
                'level': 'warning',
                'title': f'Possibly unclosed <{tag}> tag',
                'detail': f'Found {n} opening <{tag}> but {closed.get(tag, 0)} closing </{tag}>. '
                          f'An unclosed tag often makes the rest of the page disappear.',
            })
    # Stray inline event handlers are fine, but a common typo is onclick without ().
    if re.search(r'onclick\s*=\s*["\'][a-zA-Z_]\w*["\']', html):
        findings.append({
            'level': 'info',
            'title': 'Inline onclick without ()',
            'detail': 'An onclick like onclick="doThing" references the function but does not call it. '
                      'Use onclick="doThing()" to run it on click.',
        })


def _check_css(css, findings):
    if not css.strip():
        return
    # Braces balance — an extra/missing brace silently kills the rest of a stylesheet.
    opens, closes = css.count('{'), css.count('}')
    if opens != closes:
        findings.append({
            'level': 'warning',
            'title': 'Unbalanced { } in CSS',
            'detail': f'{opens} opening braces vs {closes} closing. A missing brace usually stops '
                      f'every rule after it from applying.',
        })


def _check_js(js, findings):
    if not js.strip():
        return
    # Bracket / paren / brace balance.
    for open_c, close_c, name in (('{', '}', 'curly braces'), ('(', ')', 'parentheses'), ('[', ']', 'square brackets')):
        o, c = js.count(open_c), js.count(close_c)
        if o != c:
            findings.append({
                'level': 'warning',
                'title': f'Unbalanced {name} in JavaScript',
                'detail': f'{o} “{open_c}” vs {c} “{close_c}”. A missing {name[:-1]} throws a SyntaxError '
                          f'and stops the whole script from running.',
            })
    # getElementByID — the single most common beginner typo (correct: getElementById).
    if 'getElementByID' in js:
        findings.append({
            'level': 'error',
            'title': 'Typo: getElementByID',
            'detail': 'JavaScript is case-sensitive — it is document.getElementById (lower-case “d” at the end).',
        })
    # querySelector missing the # or . selector prefix is common but not detectable safely; skip.
    # Assignment inside an if — usually meant ===.
    if re.search(r'\bif\s*\([^)=!<>]*[^=!<>]=[^=][^)]*\)', js):
        findings.append({
            'level': 'info',
            'title': 'Single = inside an if (…)',
            'detail': 'if (x = 5) assigns instead of comparing. Use == or === to compare values.',
        })
    # DOM access with no readiness guard — a top-level getElementById can run before the element exists.
    if re.search(r'document\.(getElementById|querySelector)', js) and \
       'DOMContentLoaded' not in js and 'defer' not in js:
        findings.append({
            'level': 'info',
            'title': 'Script may run before the page is ready',
            'detail': 'If this <script> is in <head>, document.getElementById can return null. '
                      'Put the script at the end of <body>, or wrap it in '
                      'document.addEventListener("DOMContentLoaded", () => { … }).',
        })
    # console.log left in — gentle nudge, not an error.
    if 'console.log' in js:
        findings.append({
            'level': 'info',
            'title': 'console.log left in',
            'detail': 'Fine while building — remember to remove debug logging before you publish.',
        })


def analyze_code(html='', css='', js='', error=''):
    """Deterministic static checks over HTML/CSS/JS. Returns a findings list.

    Never raises — a broken analyser must not block the user. Order is
    error → warning → info so the most likely culprit is first.
    """
    findings = []
    try:
        _check_html(_clip(html), findings)
        _check_css(_clip(css), findings)
        _check_js(_clip(js), findings)
        if error and error.strip():
            findings.insert(0, {
                'level': 'info',
                'title': 'Your error message',
                'detail': f'You reported: “{_clip(error).strip()[:300]}”. The checks below look for the '
                          f'common causes of that kind of error.',
            })
    except Exception:
        logger.exception('analyze_code failed')
    order = {'error': 0, 'warning': 1, 'info': 2}
    findings.sort(key=lambda f: order.get(f.get('level'), 3))
    return findings


def fix_code(html='', css='', js='', error='', allow_llm=True):
    """Return (summary, findings, source).

    Heuristic findings always come back. If a key is set, the model is given
    the code AND the findings and asked to explain/repair — grounded, never a
    blind guess. source is claude|gemini|groq|heuristic.
    """
    from .prompt_sanitize import sanitize_prompt
    html, css, js, error = (_clip(sanitize_prompt(x)) for x in (html, css, js, error))
    findings = analyze_code(html, css, js, error)

    if allow_llm:
        try:
            from .nolo_ai import configured_ai_backend, get_nolo_ai_answer
            if configured_ai_backend() != 'heuristic':
                found_txt = '\n'.join(f"- [{f['level']}] {f['title']}: {f['detail']}" for f in findings) or '- (no obvious issues found by static checks)'
                prompt = (
                    "You are helping a beginner debug a small web page. Be concise and kind.\n"
                    f"Reported error: {error or '(none given)'}\n\n"
                    f"HTML:\n{html or '(none)'}\n\nCSS:\n{css or '(none)'}\n\nJS:\n{js or '(none)'}\n\n"
                    f"Static checks already found:\n{found_txt}\n\n"
                    "Explain the most likely fix in 3-5 short sentences. Do not invent code the user did not write."
                )
                reply, source = get_nolo_ai_answer(prompt)
                if reply and source != 'heuristic':
                    return reply.strip(), findings, source
        except Exception:
            logger.exception('fix_code LLM lift failed')

    # Heuristic summary — honest, useful, no fake model.
    if findings and any(f['level'] in ('error', 'warning') for f in findings):
        summary = ("I found a few likely problems — see the list below, most-likely first. "
                   "Fix the red/orange ones and refresh the preview.")
    elif findings:
        summary = ("No obvious breakage — just a couple of tidy-ups below. "
                   "If it still misbehaves, paste the exact error message and I’ll narrow it down.")
    else:
        summary = ("My built-in checks did not spot a syntax problem. If it still isn’t working, "
                   "paste the exact error text from the browser console (F12) and I’ll look again.")
    return summary, findings, 'heuristic'


# --------------------------------------------------------------------------- #
# Skill 2: Write my README
# --------------------------------------------------------------------------- #

def _detect_features(html, css, js):
    """Human-readable feature bullets inferred from the code. No network."""
    feats = []
    blob = f"{html}\n{css}\n{js}".lower()
    checks = [
        ('localstorage', 'Saves data in the browser (localStorage) so it survives a refresh'),
        ('addeventlistener', 'Responds to user interaction with event listeners'),
        ('<form', 'Has a form for user input'),
        ('<canvas', 'Draws on an HTML canvas'),
        ('fetch(', 'Fetches data from the network'),
        ('math.random', 'Uses randomness'),
        ('@keyframes', 'Includes CSS animations'),
        ('grid-template', 'Uses CSS grid for layout'),
        ('flex', 'Uses flexbox for layout'),
    ]
    for needle, label in checks:
        if needle in blob:
            feats.append(label)
    return feats[:6]


def write_readme(title='', description='', html='', css='', js='', tech='', allow_llm=True):
    """Return (markdown, source).

    A real, structured README with no key; an LLM-written one when a key is
    set. Always meets the publish form's own gate (a '# ' heading + length).
    """
    from .prompt_sanitize import sanitize_prompt
    title = sanitize_prompt(title)[:120] or 'My Vibe'
    description = sanitize_prompt(description)[:300]
    tech = sanitize_prompt(tech)[:120]
    html, css, js = (_clip(sanitize_prompt(x)) for x in (html, css, js))

    if allow_llm:
        try:
            from .nolo_ai import configured_ai_backend, get_nolo_ai_answer
            if configured_ai_backend() != 'heuristic':
                prompt = (
                    "Write a short, friendly markdown README for a small web project.\n"
                    f"Title: {title}\nOne-liner: {description or '(none)'}\nTech: {tech or 'HTML/CSS/JS'}\n\n"
                    f"HTML:\n{html[:2000]}\n\nJS:\n{js[:2000]}\n\n"
                    "Return ONLY markdown with: a '# Title' heading, a '## What is this?' section, "
                    "a '## Features' list, and a '## How to run' section (say it runs in the browser, "
                    "no build step). Keep it under 200 words. Do not invent features the code lacks."
                )
                reply, source = get_nolo_ai_answer(prompt)
                if reply and '# ' in reply and source != 'heuristic':
                    return reply.strip()[:5000], source
        except Exception:
            logger.exception('write_readme LLM lift failed')

    # Heuristic README — genuinely structured, not a stub.
    feats = _detect_features(html, css, js)
    if not feats:
        feats = ['A small, self-contained web page']
    tech_line = tech or 'HTML, CSS, and JavaScript'
    lines = [
        f'# {title}',
        '',
        description or 'A small web project built and published on BlaqVibes.',
        '',
        '## What is this?',
        '',
        (description or f'{title} is a little web project') + f' built with {tech_line}. '
        'It runs entirely in the browser — no server and no build step.',
        '',
        '## Features',
        '',
    ]
    lines += [f'- {f}' for f in feats]
    lines += [
        '',
        '## How to run',
        '',
        'This is a static page — it runs live in the BlaqVibes sandboxed preview. '
        'To run it yourself, download the files and open `index.html` in any browser.',
        '',
        '## Built with',
        '',
        f'{tech_line}.',
    ]
    return '\n'.join(lines)[:5000], 'heuristic'
