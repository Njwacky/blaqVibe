"""Staff-only ops diagnostics, viewable from the browser.

The free tier on Render has no shell, and "it never works" problems die there:
an env var set on the wrong service, a regenerated key, an unactivated plan —
all of them look identical from outside. ``diagnose_email`` exists for the email
chain but only runs as a management command (shell required), so email had a
ground-truth probe and everything else did not.

Each check here reads the LIVE process environment (what the deployed web
service actually has, not what the Render dashboard claims), performs the real
network call, and names the broken layer. Mirrors the diagnose_email contract:
specific answer, never "nothing arrived".

Security:
- ``moderator_required``: staff log in through the usual account flow; no
  secret in the URL, nothing indexed (no robots concerns, login wall).
- The API key is only ever shown masked (first 8 / last 4 chars).
- The page makes exactly one outbound call per load (a single test query),
  so a curious moderator cannot meter-burn the account by refreshing.
"""
import html
import logging
import os

import requests

from users.decorators import moderator_required

logger = logging.getLogger(__name__)

BRAVE_TEST_ENDPOINT = "https://api.search.brave.com/res/v1/llm/context"
BRAVE_TEST_QUERY = "blaqvibes"
BRAVE_TIMEOUT = 15


def _mask_key(key):
    """xkeysib-style: enough to confirm WHICH key is loaded, safe to display."""
    if not key:
        return "(not set in this process)"
    key = str(key)
    if len(key) <= 12:
        return key[:3] + "…"
    return f"{key[:8]}…{key[-4:]} (len={len(key)})"


def _page(title, rows, ok):
    """Tiny self-contained page: monospace table, one colour-coded verdict."""
    body = "\n".join(
        f"<tr><td class=k>{html.escape(str(k))}</td><td>{v}</td></tr>"
        for k, v in rows
    )
    badge = ("<span class=ok>WORKING</span>" if ok
             else "<span class=bad>BROKEN</span>")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<style>
 body{{background:#0b0b10;color:#e8e6f0;font:15px/1.55 ui-monospace,Menlo,Consolas,monospace;
      max-width:860px;margin:40px auto;padding:0 16px}}
 h1{{font-size:20px;margin-bottom:4px}}
 .sub{{color:#8f8ca3;margin-bottom:24px}}
 table{{border-collapse:collapse;width:100%}}
 td{{padding:8px 12px;border-bottom:1px solid #23212e;vertical-align:top}}
 td.k{{color:#8f8ca3;white-space:nowrap;width:240px}}
 .ok{{color:#4ade80;font-weight:700}}
 .bad{{color:#f87171;font-weight:700}}
 code{{color:#c4b5fd}}
</style></head>
<body>
<h1>{html.escape(title)} {badge}</h1>
<div class=sub>Staff-only diagnostic · reads the LIVE web-service environment</div>
<table>{body}</table>
</body></html>"""


def _respond(request, title, rows, ok):
    from django.http import HttpResponse
    if not ok:
        # Ops marker: filter the Render log for this to find every bad check.
        logger.error("BRAVE_CHECK_FAILED %s", title)
    else:
        logger.info("BRAVE_CHECK_OK %s", title)
    return HttpResponse(_page(title, rows, ok))


@moderator_required
def brave_check(request):
    """Is BRAVE_API_KEY alive in this process, and does Brave accept it?

    Walks the chain and stops at the first broken link:
      1. key present in the running process (wrong service / stale container → "(not set)")
      2. key accepted by Brave (401 → wrong/regenerated key)
      3. plan activated (400/403 → Search plan not active)
      4. results actually come back (grounding non-empty)
    """
    key = (os.getenv("BRAVE_API_KEY") or "").strip()
    rows = [
        ("BRAVE_API_KEY (live process)", _mask_key(key)),
        ("endpoint", f"GET {html.escape(BRAVE_TEST_ENDPOINT)}"),
    ]

    if not key:
        rows.append(("verdict",
            "No key in this process. If Render shows BRAVE_API_KEY, it is set on a "
            "different service (worker vs web), misspelled, or the container predates "
            "the change — re-save the env vars on the <b>web</b> service to force a "
            "restart."))
        return _respond(request, "Brave Search check", rows, ok=False)

    try:
        resp = requests.get(
            BRAVE_TEST_ENDPOINT,
            params={"q": BRAVE_TEST_QUERY},
            headers={"X-Subscription-Token": key},
            timeout=BRAVE_TIMEOUT,
        )
    except requests.RequestException as exc:
        rows.append(("request", f"FAILED — {type(exc).__name__}: {html.escape(str(exc))}"))
        rows.append(("verdict",
            "Could not reach Brave at all (network/DNS from the web service)."))
        return _respond(request, "Brave Search check", rows, ok=False)

    rows.append(("HTTP status", str(resp.status_code)))

    if resp.status_code != 200:
        rows.append(("Brave says", html.escape(resp.text[:500]) or "(empty body)"))
        hint = {
            401: "401 — the key is wrong, expired, or was regenerated. Re-copy it from "
                 "the Brave dashboard (API Keys) and update the Render variable.",
            400: "400 — Brave received the key but refused the call: the Search plan is "
                 "probably not activated (or the key belongs to an account without it). "
                 "Check Plans in the Brave dashboard.",
            403: "403 — the key is valid but not permitted for this endpoint: make sure "
                 "the <b>Search</b> plan is active (it covers Web Search + LLM Context).",
            429: "429 — rate limited or monthly free credits exhausted. Check usage in "
                 "the Brave dashboard.",
        }.get(resp.status_code,
              f"Status {resp.status_code} — check the Brave dashboard and API docs.")
        rows.append(("verdict", html.escape(hint)))
        return _respond(request, "Brave Search check", rows, ok=False)

    try:
        data = resp.json()
        snippets = (data.get("grounding") or {}).get("generic") or []
    except ValueError:
        data, snippets = {}, []
        rows.append(("verdict",
            "200 but the body is not JSON — unusual; check Brave status page."))
        return _respond(request, "Brave Search check", rows, ok=False)

    if not snippets:
        rows.append(("verdict",
            "Key accepted, but no results came back for the test query. The key works; "
            "check the Brave dashboard for plan/usage state."))
        return _respond(request, "Brave Search check", rows, ok=False)

    first = snippets[0]
    rows.append(("sample result",
                 f"{html.escape(str(first.get('title', '')))} — "
                 f"{html.escape(str(first.get('url', '')))}"))
    rows.append(("verdict",
        "Everything works: key is in this process, Brave accepts it, and results come "
        "back. Whatever feature should use it just isn't calling it yet (see the code "
        "that reads <code>BRAVE_API_KEY</code>)." ))
    return _respond(request, "Brave Search check", rows, ok=True)
