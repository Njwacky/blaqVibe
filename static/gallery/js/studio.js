(function () {
  'use strict';

  var cfg = window.STUDIO || {};
  var canPreview = cfg.canPreview === true;
  var draftKey = 'blaq-studio-draft:' + (cfg.draftKey || 'blank');
  var DRAFT_MAX = 180000;

  var ed = {
    html: document.getElementById('ed-html'),
    css: document.getElementById('ed-css'),
    js: document.getElementById('ed-js'),
  };
  if (!ed.html) return;

  var frame = document.getElementById('studio-frame');
  var previewLive = canPreview && !!frame;

  function buildDocument() {
    var css = ed.css ? ed.css.value : '';
    var js = ed.js ? ed.js.value : '';
    var html = ed.html ? ed.html.value : '';
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
    if (!previewLive) return;
    frame.srcdoc = buildDocument();
  }

  function readDraft() {
    try {
      var raw = sessionStorage.getItem(draftKey);
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || typeof data !== 'object') return null;
      return data;
    } catch (e) {
      return null;
    }
  }

  function writeDraft() {
    try {
      var payload = JSON.stringify({
        html: ed.html ? ed.html.value : '',
        css: ed.css ? ed.css.value : '',
        js: ed.js ? ed.js.value : '',
      });
      if (payload.length > DRAFT_MAX) return;
      sessionStorage.setItem(draftKey, payload);
    } catch (e) {

    }
  }

  function restoreDraft() {
    var data = readDraft();
    if (!data) return;
    if (typeof data.html === 'string' && ed.html) ed.html.value = data.html;
    if (typeof data.css === 'string' && ed.css) ed.css.value = data.css;
    if (typeof data.js === 'string' && ed.js) ed.js.value = data.js;
  }

  restoreDraft();

  var timer = null;
  function scheduleRender() {
    writeDraft();
    if (!previewLive) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(render, 350);
  }
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
  ['html', 'css', 'js'].forEach(function (k) {
    if (ed[k]) ed[k].addEventListener('input', scheduleRender);
  });
  ['html', 'css', 'js'].forEach(function (k) {
    if (!ed[k]) return;
    ed[k].addEventListener('keydown', function (e) {
      if (e.key !== 'Tab') return;
      e.preventDefault();
      var el = e.target;
      var s = el.selectionStart, en = el.selectionEnd;
      el.value = el.value.slice(0, s) + '  ' + el.value.slice(en);
      el.selectionStart = el.selectionEnd = s + 2;
      writeDraft();
    });
  });

  var runBtn = document.getElementById('studio-run');
  if (runBtn && previewLive) runBtn.addEventListener('click', render);
  var drawer = document.getElementById('studio-publish');
  var openBtn = document.getElementById('studio-open-publish');
  var closeBtns = document.querySelectorAll('[data-close-drawer]');
  var lastFocused = null;

  function openDrawer() {
    if (!drawer) return;
    lastFocused = document.activeElement;
    drawer.hidden = false;
    document.body.classList.add('studio-drawer-open');
    var first = drawer.querySelector('input:not([type="hidden"]), select, textarea')
      || drawer.querySelector('button');
    if (first && first.focus) first.focus();
  }
  function closeDrawer() {
    if (!drawer) return;
    drawer.hidden = true;
    document.body.classList.remove('studio-drawer-open');
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }
  if (openBtn) openBtn.addEventListener('click', openDrawer);
  closeBtns.forEach(function (b) { b.addEventListener('click', closeDrawer); });
  if (drawer) drawer.addEventListener('click', function (e) {
    if (e.target === drawer) closeDrawer();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' && e.key !== 'Esc') return;
    if (drawer && !drawer.hidden) { closeDrawer(); return; }
    if (noloBox && !noloBox.hidden) noloBox.hidden = true;
  });
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
  var form = document.getElementById('studio-form');
  if (form) {
    form.addEventListener('submit', function () {
      var h = document.getElementById('submit-html');
      var c = document.getElementById('submit-css');
      var j = document.getElementById('submit-js');
      if (h) h.value = ed.html ? ed.html.value : '';
      if (c) c.value = ed.css ? ed.css.value : '';
      if (j) j.value = ed.js ? ed.js.value : '';
      try { sessionStorage.removeItem(draftKey); } catch (e) {}
    });
  }

  render();
})();
