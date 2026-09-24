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

# Render injects these into every service it runs (COMMIT only for git-backed
# deploys). Naming the serving service turns "I set it in Render" into a
# checkable fact: if the Environment tab they edited belongs to a different
# service in the same project, the name shown here won't match it.
RENDER_META_VARS = (
    "RENDER_SERVICE_NAME",
    "RENDER_ENVIRONMENT",
    "RENDER_GIT_COMMIT",
)


def _mask_key(key):
    """xkeysib-style: enough to confirm WHICH key is loaded, safe to display."""
    if not key:
        return "(not set in this process)"
    key = str(key)
    if len(key) <= 12:
        return key[:3] + "…"
    return f"{key[:8]}…{key[-4:]} (len={len(key)})"


def _render_rows():
    """Ground truth about which Render service is serving this page.

    Render injects RENDER_* vars into every service it runs. When a key is
    "set in Render" but missing from the live process, the usual cause is
    that it was set on a different service in the same project (worker vs
    web) — showing the serving service's name settles that without shell
    access (the free tier has none). The commit makes a stale container
    visible: a deploy that failed mid-way leaves the previous container
    running, and its SHA will differ from the Deploys tab's latest.
    """
    rows = []
    for var in RENDER_META_VARS:
        val = (os.getenv(var) or "").strip()
        if var == "RENDER_GIT_COMMIT" and val:
            val = f"{val[:12]} — compare with the Deploys tab in Render"
        rows.append((var, html.escape(val) if val
                     else "(not set — not on Render, or deployed from an image)"))
    return rows


def _brave_near_misses():
    """Env var NAMES containing "brave" that aren't the exact key — never values.

    Catches "BRAVE_APIKEY", "brave_api_key", "BRAVE_KEY", a trailing space in
    the name: all of these read as "I set it in Render" from the dashboard
    while the code, which reads exactly BRAVE_API_KEY, sees nothing.
    """
    hits = [n for n in os.environ if "BRAVE" in n.upper() and n != "BRAVE_API_KEY"]
    return sorted(
        f"<code>{html.escape(n)}</code>"
        + (" ← whitespace in the name" if n != n.strip() else "")
        for n in hits
    )


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
      1. key present in the running process — and if not, names WHICH failure:
         blank value (variable saved empty), misspelled name (near-miss env
         names listed), wrong service (the serving Render service is named so
         the Environment tab they edited can be compared), or a stale
         container (live RENDER_GIT_COMMIT shown to compare with Deploys)
      2. key accepted by Brave (401 → wrong/regenerated key)
      3. plan activated (400/403 → Search plan not active)
      4. results actually come back (grounding non-empty)
    """
    raw = os.getenv("BRAVE_API_KEY")
    key = (raw or "").strip()
    rows = [
        ("BRAVE_API_KEY (live process)", _mask_key(key)),
        ("endpoint", f"GET {html.escape(BRAVE_TEST_ENDPOINT)}"),
    ]
    rows.extend(_render_rows())

    if not key:
        if raw is not None:
            verdict = (
                "BRAVE_API_KEY exists in this process but its value is empty or blank — "
                "the Render variable was saved without the key in it. Paste the key from "
                "the Brave dashboard (API Keys) into the value on the web service, then "
                "Save Changes.")
        else:
            near = _brave_near_misses()
            if near:
                rows.append(("similar names in this process", "; ".join(near)))
                verdict = (
                    "The variable name is misspelled. Render did set <b>something</b> — "
                    "the names above are in the live process, but the code reads exactly "
                    "<code>BRAVE_API_KEY</code>. Rename the variable on the web service's "
                    "Environment tab, then Save Changes.")
            else:
                service = (os.getenv("RENDER_SERVICE_NAME") or "").strip()
                if service:
                    verdict = (
                        f"No key in this process — and this page is served by "
                        f"<b>{html.escape(service)}</b>. So BRAVE_API_KEY is either set "
                        "on a different service in the Render project (open the "
                        "Environment tab you edited and check the service name at the "
                        "top matches this one), or the deploy that added it never "
                        "finished — a failed deploy leaves the old container running "
                        "(check the Deploys tab; the commit listed above is what is "
                        "actually live). Re-saving the env vars on the "
                        f"<b>{html.escape(service)}</b> service forces a restart.")
                else:
                    verdict = (
                        "No key in this process. If Render shows BRAVE_API_KEY, it is "
                        "set on a different service (worker vs web), misspelled, or the "
                        "container predates the change — re-save the env vars on the "
                        "<b>web</b> service to force a restart.")
        rows.append(("verdict", verdict))
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
