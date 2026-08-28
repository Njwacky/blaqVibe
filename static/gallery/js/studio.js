/* BlaqVibes Studio — in-browser editor with a live, client-side sandboxed preview.
 *
 * 5 Whys (mirrored from gallery/views_community.studio):
 * 1. Why srcdoc in a sandboxed iframe, not a server round-trip per keystroke?
 *    `<iframe sandbox="allow-scripts" srcdoc>` is an opaque origin — the
 *    user's in-progress code cannot read cookies or the parent DOM — so we
 *    can run it instantly, locally, with zero latency and zero server load.
 * 2. Why NOT allow-same-origin on the frame? That would give the previewed
 *    code our origin (cookies, storage). allow-scripts alone keeps it opaque.
 * 3. Why debounce the auto-refresh? Rebuilding the document on every keystroke
 *    janks the editor; a short debounce keeps typing smooth and the preview
 *    near-live.
 * 4. Why mirror the editors into hidden inputs only at submit? The server form
 *    is the single publish path; the editors are the source of truth until the
 *    moment we hand their text to that form.
 * 5. Why no external editor library? A plain <textarea> has zero supply-chain
 *    surface and works with keyboard and screen readers out of the box — the
 *    audience is beginners, not power users who need a full IDE.
 */
(function () {
  'use strict';

  var ed = {
    html: document.getElementById('ed-html'),
    css: document.getElementById('ed-css'),
    js: document.getElementById('ed-js'),
  };
  var frame = document.getElementById('studio-frame');
  if (!frame || !ed.html) return;

  function buildDocument() {
    var css = ed.css ? ed.css.value : '';
    var js = ed.js ? ed.js.value : '';
    var html = ed.html ? ed.html.value : '';
    // One self-contained document — same shape as a published snippet.
    return (
      '<!DOCTYPE html><html><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width, initial-scale=1.0">' +
      '<style>' + css + '</style></head><body>' +
      html +
      '<' + 'script>' + js + '<' + '/script>' +
      '</body></html>'
    );
  }

  function render() {
    // srcdoc keeps the frame at an opaque origin (no allow-same-origin).
    frame.srcdoc = buildDocument();
  }

  var timer = null;
  function scheduleRender() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(render, 350);
  }

  // Tabs: show one editor pane at a time.
  var tabs = document.querySelectorAll('.studio-tab');
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var name = tab.getAttribute('data-tab');
      tabs.forEach(function (t) { t.classList.toggle('on', t === tab); });
      ['html', 'css', 'js'].forEach(function (k) {
        if (ed[k]) ed[k].hidden = (k !== name);
      });
    });
  });

  // Live preview as you type.
  ['html', 'css', 'js'].forEach(function (k) {
    if (ed[k]) ed[k].addEventListener('input', scheduleRender);
  });

  // Tab key inserts two spaces instead of leaving the textarea.
  ['html', 'css', 'js'].forEach(function (k) {
    if (!ed[k]) return;
    ed[k].addEventListener('keydown', function (e) {
      if (e.key !== 'Tab') return;
      e.preventDefault();
      var el = e.target;
      var s = el.selectionStart, en = el.selectionEnd;
      el.value = el.value.slice(0, s) + '  ' + el.value.slice(en);
      el.selectionStart = el.selectionEnd = s + 2;
    });
  });

  var runBtn = document.getElementById('studio-run');
  if (runBtn) runBtn.addEventListener('click', render);

  // Publish drawer open/close.
  var drawer = document.getElementById('studio-publish');
  var openBtn = document.getElementById('studio-open-publish');
  var closeBtn = document.getElementById('studio-close-publish');
  function openDrawer() { if (drawer) drawer.hidden = false; }
  function closeDrawer() { if (drawer) drawer.hidden = true; }
  if (openBtn) openBtn.addEventListener('click', openDrawer);
  if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
  if (drawer) drawer.addEventListener('click', function (e) {
    if (e.target === drawer) closeDrawer();
  });

  // --- Nolo: fix my code ---------------------------------------------------
  var cfg = window.STUDIO || {};
  var noloBox = document.getElementById('studio-nolo');
  var noloSummary = document.getElementById('studio-nolo-summary');
  var noloFindings = document.getElementById('studio-nolo-findings');
  var noloSource = document.getElementById('studio-nolo-source');
  var noloClose = document.getElementById('studio-nolo-close');
  var fixBtn = document.getElementById('studio-fix');

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function sourceLabel(src) {
    return src && src !== 'heuristic' ? '(answered by ' + esc(src) + ')' : '(built-in checks — no AI key set)';
  }

  function renderFindings(findings) {
    noloFindings.innerHTML = '';
    if (!findings || !findings.length) {
      noloFindings.innerHTML = '<div class="studio-finding info"><b>No issues spotted.</b> Nice.</div>';
      return;
    }
    findings.forEach(function (f) {
      var div = document.createElement('div');
      div.className = 'studio-finding ' + (f.level || 'info');
      var icon = f.level === 'error' ? '⛔' : (f.level === 'warning' ? '⚠️' : 'ℹ️');
      div.innerHTML = '<b>' + icon + ' ' + esc(f.title) + '</b><div>' + esc(f.detail) + '</div>';
      noloFindings.appendChild(div);
    });
  }

  if (fixBtn) {
    fixBtn.addEventListener('click', function () {
      fixBtn.disabled = true;
      fixBtn.textContent = '🔧 Nolo is looking…';
      fetch(cfg.fixUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrf },
        body: JSON.stringify({
          html: ed.html ? ed.html.value : '',
          css: ed.css ? ed.css.value : '',
          js: ed.js ? ed.js.value : '',
        }),
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.error) { noloSummary.textContent = data.error; }
        else {
          noloSummary.textContent = data.summary || '';
          noloSource.textContent = sourceLabel(data.source);
          renderFindings(data.findings);
        }
        if (noloBox) { noloBox.hidden = false; noloBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
      }).catch(function () {
        noloSummary.textContent = 'Network error — try again.';
        if (noloBox) noloBox.hidden = false;
      }).finally(function () {
        fixBtn.disabled = false;
        fixBtn.textContent = '🔧 Nolo: fix my code';
      });
    });
  }
  if (noloClose) noloClose.addEventListener('click', function () { if (noloBox) noloBox.hidden = true; });

  // --- Nolo: write my README ----------------------------------------------
  var readmeBtn = document.getElementById('studio-readme');
  if (readmeBtn) {
    readmeBtn.addEventListener('click', function () {
      var titleEl = document.getElementById('id_title');
      var descEl = document.getElementById('id_short_description');
      var techEl = document.getElementById('id_tech_stack');
      var readmeEl = document.getElementById('id_readme');
      readmeBtn.disabled = true;
      readmeBtn.textContent = '✍️ Writing…';
      fetch(cfg.readmeUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cfg.csrf },
        body: JSON.stringify({
          title: titleEl ? titleEl.value : '',
          description: descEl ? descEl.value : '',
          tech: techEl ? techEl.value : '',
          html: ed.html ? ed.html.value : '',
          css: ed.css ? ed.css.value : '',
          js: ed.js ? ed.js.value : '',
        }),
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.readme && readmeEl) readmeEl.value = data.readme;
      }).catch(function () {}).finally(function () {
        readmeBtn.disabled = false;
        readmeBtn.textContent = '✍️ Nolo: write it for me';
      });
    });
  }

  // On submit, copy the live editors into the hidden fields the form sends.
  var form = document.getElementById('studio-form');
  if (form) {
    form.addEventListener('submit', function () {
      var h = document.getElementById('submit-html');
      var c = document.getElementById('submit-css');
      var j = document.getElementById('submit-js');
      if (h) h.value = ed.html ? ed.html.value : '';
      if (c) c.value = ed.css ? ed.css.value : '';
      if (j) j.value = ed.js ? ed.js.value : '';
    });
  }

  render();
})();
