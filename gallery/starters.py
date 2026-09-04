"""Curated starter templates — the on-ramp for a vibe coder with nothing yet.

Data, not user content. Each starter is a tiny, self-contained HTML/CSS/JS
snippet a beginner can open in the in-browser Studio, write in the editors
without an account, run a live preview once signed in, and publish through
the SAME publish path every upload uses (so scan, classify, and trust all
still apply).

5 Whys — why curated data instead of, say, a folder of files or DB rows?

1. Why hard-coded data? A starter must be trustworthy the instant it loads —
   no upload, no scan pending, no user-supplied bytes. Code-reviewed data is
   the only source we can promise is clean on first paint.
2. Why snippets (html/css/js), not ZIPs? The Studio's whole point is instant
   client-side editing; three text fields map straight onto
   AppProject.html_code/css_code/js_code and the existing snippet preview.
   A ZIP would need a build step the beginner does not have. The live
   sandbox still runs those three fields — it just waits for a signed-in
   session before the iframe exists.
3. Why route publish through the normal form path? A starter the user edited
   is user content the moment they change it — it must be scanned, classified,
   and trust-graded like anything else. A shortcut that skipped that would be
   a hole exactly where beginners (who trust us most) live.
4. Why keep them tiny and framework-light? The audience is someone who has
   never shipped. A 40-line page they can read top to bottom teaches; a
   bundled SPA hides the very thing they came to learn.
5. Why a stable `slug` per starter? The Studio URL is /studio/<slug>/ and the
   "open this starter" links across the site point at it; a stable id keeps
   those links valid and lets tests target one starter deterministically.
"""

STARTERS_VERSION = "2026-08-30"


def _dedent(block: str) -> str:
    """Trim a leading newline so triple-quoted blocks read cleanly."""
    return block.lstrip("\n")


STARTERS = (
    {
        "slug": "hello-landing",
        "name": "Hello Landing Page",
        "emoji": "🚀",
        "blurb": "A clean one-screen landing page with a headline, tagline, and call-to-action button.",
        "tags": ("landing", "html", "css"),
        "tech_stack": "HTML, CSS",
        "category_type": "snippet",
        "readme": _dedent(
            """
# Hello Landing Page

A tiny, single-screen landing page — a headline, a tagline, and one clear
call-to-action button. Everything is plain HTML and CSS, so it is easy to
read and easy to change.

## What to try
- Change the headline and tagline text.
- Swap the two gradient colours near the top of the CSS.
- Point the button at your own link.

## How it runs
Pure HTML/CSS/JS — it runs live in the BlaqVibes sandboxed preview and needs
no build step.
"""
        ),
        "html": _dedent(
            """
<main class="wrap">
  <span class="pill">✦ Built on BlaqVibes</span>
  <h1>Ship the thing you vibe-coded.</h1>
  <p>Stop letting good ideas rot on your laptop. Publish it, let people remix it, and watch it climb the feed.</p>
  <a class="cta" href="#" onclick="celebrate();return false;">Get started →</a>
  <p class="count" id="count">0 people vibing</p>
</main>
"""
        ),
        "css": _dedent(
            """
:root { --a:#7c3aed; --b:#2563eb; }
* { box-sizing:border-box; margin:0; }
body { font-family:system-ui,Segoe UI,Roboto,sans-serif; background:radial-gradient(1200px 600px at 50% -10%, #1e1b4b, #0b1020); color:#e5e7eb; min-height:100vh; display:grid; place-items:center; }
.wrap { text-align:center; padding:32px; max-width:560px; }
.pill { display:inline-block; font-size:12px; letter-spacing:.08em; padding:6px 12px; border-radius:999px; background:rgba(124,58,237,.18); border:1px solid rgba(124,58,237,.5); }
h1 { font-size:clamp(28px,6vw,48px); line-height:1.05; margin:18px 0 12px; background:linear-gradient(90deg,var(--a),var(--b)); -webkit-background-clip:text; background-clip:text; color:transparent; }
p { color:#9ca3af; font-size:16px; line-height:1.6; }
.cta { display:inline-block; margin-top:22px; padding:12px 22px; border-radius:12px; font-weight:700; color:#fff; text-decoration:none; background:linear-gradient(90deg,var(--a),var(--b)); }
.cta:active { transform:translateY(1px); }
.count { margin-top:16px; font-size:13px; }
"""
        ),
        "js": _dedent(
            """
let n = 0;
function celebrate() {
  n += 1;
  document.getElementById('count').textContent = n + (n === 1 ? ' person vibing' : ' people vibing');
}
"""
        ),
    },
    {
        "slug": "counter-app",
        "name": "Click Counter",
        "emoji": "🔢",
        "blurb": "The classic first app — buttons that add, subtract, and reset a number. Learn events and state.",
        "tags": ("beginner", "javascript", "interactive"),
        "tech_stack": "HTML, CSS, JavaScript",
        "category_type": "snippet",
        "readme": _dedent(
            """
# Click Counter

The classic first interactive app: three buttons change a number on screen.
It is the smallest useful example of the loop every app runs on — an event
happens, the state changes, the screen updates.

## What to try
- Change the step from 1 to 5.
- Add a button that doubles the number.
- Stop the count going below zero.

## How it runs
Plain HTML/CSS/JS — runs live in the sandboxed preview, no build step.
"""
        ),
        "html": _dedent(
            """
<main class="card">
  <h1>Click Counter</h1>
  <div class="value" id="value">0</div>
  <div class="row">
    <button onclick="step(-1)">−1</button>
    <button class="reset" onclick="reset()">Reset</button>
    <button onclick="step(1)">+1</button>
  </div>
  <p class="hint">Your first bit of state. Tweak the code on the left.</p>
</main>
"""
        ),
        "css": _dedent(
            """
* { box-sizing:border-box; margin:0; }
body { font-family:system-ui,sans-serif; background:#0b1020; color:#e5e7eb; min-height:100vh; display:grid; place-items:center; }
.card { background:#111827; border:1px solid #1f2937; border-radius:20px; padding:32px 28px; text-align:center; width:min(90vw,360px); }
h1 { font-size:20px; margin-bottom:14px; }
.value { font-size:64px; font-weight:800; margin:8px 0 20px; color:#38bdf8; }
.row { display:flex; gap:10px; justify-content:center; }
button { flex:1; padding:12px; border:0; border-radius:12px; font-size:16px; font-weight:700; cursor:pointer; background:#38bdf8; color:#0b1020; }
button.reset { background:#374151; color:#e5e7eb; }
button:active { transform:translateY(1px); }
.hint { margin-top:18px; font-size:13px; color:#6b7280; }
"""
        ),
        "js": _dedent(
            """
let count = 0;
const el = document.getElementById('value');
function render() { el.textContent = count; }
function step(by) { count += by; render(); }
function reset() { count = 0; render(); }
render();
"""
        ),
    },
    {
        "slug": "todo-list",
        "name": "To-Do List",
        "emoji": "✅",
        "blurb": "Add tasks, tick them off, delete them. A real little app that saves to your browser.",
        "tags": ("app", "javascript", "localstorage"),
        "tech_stack": "HTML, CSS, JavaScript",
        "category_type": "snippet",
        "readme": _dedent(
            """
# To-Do List

Add tasks, tick them off, delete them — and the list survives a refresh
because it saves to the browser's localStorage. A complete tiny app that
shows input handling, rendering a list, and persistence.

## What to try
- Add a "clear completed" button.
- Show a count of tasks left.
- Change what happens when the list is empty.

## How it runs
Plain HTML/CSS/JS — runs live in the sandboxed preview, no build step.
"""
        ),
        "html": _dedent(
            """
<main class="app">
  <h1>✅ To-Do</h1>
  <form id="form" autocomplete="off">
    <input id="input" placeholder="What needs doing?" maxlength="120">
    <button>Add</button>
  </form>
  <ul id="list"></ul>
  <p class="empty" id="empty">Nothing yet — add your first task.</p>
</main>
"""
        ),
        "css": _dedent(
            """
* { box-sizing:border-box; margin:0; }
body { font-family:system-ui,sans-serif; background:#0b1020; color:#e5e7eb; min-height:100vh; display:grid; place-items:start center; padding:40px 16px; }
.app { width:min(92vw,440px); }
h1 { font-size:22px; margin-bottom:14px; }
form { display:flex; gap:8px; }
input { flex:1; padding:12px; border-radius:12px; border:1px solid #1f2937; background:#111827; color:#e5e7eb; font-size:15px; }
button { padding:12px 16px; border:0; border-radius:12px; background:#7c3aed; color:#fff; font-weight:700; cursor:pointer; }
ul { list-style:none; margin-top:16px; display:grid; gap:8px; }
li { display:flex; align-items:center; gap:10px; background:#111827; border:1px solid #1f2937; border-radius:12px; padding:10px 12px; }
li.done span { text-decoration:line-through; color:#6b7280; }
li span { flex:1; cursor:pointer; }
li .del { background:none; border:0; color:#ef4444; font-size:18px; cursor:pointer; padding:0 4px; }
.empty { color:#6b7280; font-size:13px; margin-top:14px; }
"""
        ),
        "js": _dedent(
            """
const KEY = 'blaqvibes-todo';
let items = JSON.parse(localStorage.getItem(KEY) || '[]');
const list = document.getElementById('list');
const empty = document.getElementById('empty');
function save() { localStorage.setItem(KEY, JSON.stringify(items)); }
function render() {
  list.innerHTML = '';
  empty.style.display = items.length ? 'none' : 'block';
  items.forEach((it, i) => {
    const li = document.createElement('li');
    if (it.done) li.className = 'done';
    const span = document.createElement('span');
    span.textContent = it.text;
    span.onclick = () => { items[i].done = !items[i].done; save(); render(); };
    const del = document.createElement('button');
    del.className = 'del'; del.textContent = '×';
    del.onclick = () => { items.splice(i, 1); save(); render(); };
    li.append(span, del);
    list.append(li);
  });
}
document.getElementById('form').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text) return;
  items.push({ text, done: false });
  input.value = ''; save(); render();
});
render();
"""
        ),
    },
    {
        "slug": "quote-generator",
        "name": "Random Quote",
        "emoji": "💬",
        "blurb": "Show a random quote on a button press. Learn arrays, randomness, and updating the DOM.",
        "tags": ("beginner", "javascript"),
        "tech_stack": "HTML, CSS, JavaScript",
        "category_type": "snippet",
        "readme": _dedent(
            """
# Random Quote

Press the button, get a random quote. A friendly first look at arrays,
`Math.random`, and swapping text on the page.

## What to try
- Add your own quotes to the list.
- Make the background colour change with each quote.
- Add a "copy" button.

## How it runs
Plain HTML/CSS/JS — runs live in the sandboxed preview, no build step.
"""
        ),
        "html": _dedent(
            """
<main class="card">
  <p class="quote" id="quote">Click the button for a little wisdom.</p>
  <p class="who" id="who"></p>
  <button onclick="next()">New quote →</button>
</main>
"""
        ),
        "css": _dedent(
            """
* { box-sizing:border-box; margin:0; }
body { font-family:Georgia,serif; background:linear-gradient(135deg,#111827,#1e1b4b); color:#e5e7eb; min-height:100vh; display:grid; place-items:center; padding:20px; }
.card { background:rgba(17,24,39,.7); border:1px solid #312e81; border-radius:20px; padding:36px 30px; max-width:520px; text-align:center; }
.quote { font-size:22px; line-height:1.5; }
.who { margin-top:12px; color:#a5b4fc; font-style:italic; min-height:20px; }
button { margin-top:22px; padding:11px 20px; border:0; border-radius:12px; background:#6366f1; color:#fff; font-family:system-ui,sans-serif; font-weight:700; cursor:pointer; }
button:active { transform:translateY(1px); }
"""
        ),
        "js": _dedent(
            """
const quotes = [
  { text: 'Done is better than perfect.', who: 'Sheryl Sandberg' },
  { text: 'The best way to predict the future is to invent it.', who: 'Alan Kay' },
  { text: 'Simplicity is the soul of efficiency.', who: 'Austin Freeman' },
  { text: 'First, solve the problem. Then, write the code.', who: 'John Johnson' },
  { text: 'Make it work, make it right, make it fast.', who: 'Kent Beck' },
];
function next() {
  const q = quotes[Math.floor(Math.random() * quotes.length)];
  document.getElementById('quote').textContent = '“' + q.text + '”';
  document.getElementById('who').textContent = '— ' + q.who;
}
next();
"""
        ),
    },
)

STARTERS_BY_SLUG = {s["slug"]: s for s in STARTERS}


def get_starter(slug):
    """Return one starter dict or None. The only lookup callers should use."""
    return STARTERS_BY_SLUG.get((slug or "").strip())
