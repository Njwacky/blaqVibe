/* Quarantine clean-up — selection, live search, and the typed confirmation.

   Everything in this file is convenience. With JavaScript off the list is
   still a real POST form (tick, press "Review the ticked accounts"), the
   search is still a real GET form, and the server re-checks every id, the
   typed phrase and the reason. Nothing here is a security boundary.

   What it adds:
     list page    - a selection that SURVIVES searching and paging (kept in
                    sessionStorage for this tab), capped at the server's batch
                    limit; select-all-on-page; shift-click range select;
                    search that updates as you type (debounced) by swapping the
                    results block instead of reloading the site around it.
     review page  - the delete button stays disabled until the phrase and a
                    reason are filled in, and locks itself after one click. */
(function () {
  'use strict';

  var STORAGE_KEY = 'bv-qa-selected';
  var TYPING_DELAY_MS = 300;
  // The server's own links leave these out (users/account_cleanup.Filters.params),
  // so the address bar shows the same short, shareable URL either way.
  var DEFAULTS = { show: 'all', sort: 'held', per: '50' };

  function say(message) {
    if (typeof window.toast === 'function') { window.toast(message); }
  }

  /* ------------------------------------------------------------------ list */
  function initList(root) {
    var results = document.getElementById('qa-results');
    var filters = document.getElementById('qa-filters');
    var max = parseInt(root.getAttribute('data-max'), 10) || 100;
    var listUrl = root.getAttribute('data-list-url') || window.location.pathname;
    var selected = readSelection();
    var lastClicked = null;
    var requestId = 0;
    var inflight = null;
    var typingTimer = null;

    function readSelection() {
      try {
        var raw = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || '[]');
        if (!Array.isArray(raw)) { return new Set(); }
        // Storage is input like any other: only plain ids come back out of it, and
        // never more than the batch limit. The server checks them again regardless.
        var ids = raw.map(String).filter(function (id) { return /^[0-9]{1,19}$/.test(id); });
        return new Set(ids.slice(0, max));
      } catch (e) { return new Set(); }
    }
    function writeSelection() {
      try { window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(selected))); } catch (e) { /* private mode */ }
    }
    function checks() { return Array.prototype.slice.call(results.querySelectorAll('.qa-check')); }

    // The server sends ?cleared=1 after a delete: forget what was ticked, and
    // take the flag out of the address bar so a refresh does not repeat it.
    if (root.getAttribute('data-reset') === '1') {
      selected = new Set();
      writeSelection();
      try {
        var here = new URL(window.location.href);
        here.searchParams.delete('cleared');
        window.history.replaceState(null, '', here.pathname + here.search + here.hash);
      } catch (e) { /* older browser: harmless */ }
    }

    // Returns false when the batch limit stops the add.
    function setOne(id, on) {
      if (!on) { selected.delete(id); return true; }
      if (selected.has(id)) { return true; }
      if (selected.size >= max) { return false; }
      selected.add(id);
      return true;
    }

    function sync() {
      var boxes = checks();
      var onPage = 0;
      boxes.forEach(function (box) {
        var on = selected.has(box.value);
        box.checked = on;
        var row = box.closest('[data-qa-row]');
        if (row) { row.classList.toggle('is-selected', on); }
        if (on) { onPage += 1; }
      });
      var bar = results.querySelector('[data-qa-bar]');
      if (bar) {
        bar.hidden = selected.size === 0;
        var count = bar.querySelector('[data-qa-count]');
        if (count) { count.textContent = String(selected.size); }
        var hint = bar.querySelector('[data-qa-hint]');
        if (hint) {
          if (selected.size > onPage) {
            hint.textContent = ' (' + (selected.size - onPage) + ' from other pages or searches)';
          } else if (selected.size >= max) {
            hint.textContent = ' (batch limit reached)';
          } else {
            hint.textContent = '';
          }
        }
      }
      var all = results.querySelector('[data-qa-all]');
      if (all) {
        all.checked = boxes.length > 0 && onPage === boxes.length;
        all.indeterminate = onPage > 0 && onPage < boxes.length;
      }
    }

    function limitHit() { say('Limit reached: ' + max + ' accounts per batch.'); }

    // Shift-click selects the whole range between this box and the last one.
    results.addEventListener('click', function (event) {
      var box = event.target.closest ? event.target.closest('.qa-check') : null;
      if (!box) { return; }
      if (event.shiftKey && lastClicked && lastClicked !== box) {
        var boxes = checks();
        var from = boxes.indexOf(lastClicked);
        var to = boxes.indexOf(box);
        if (from > -1 && to > -1) {
          var blocked = false;
          for (var i = Math.min(from, to); i <= Math.max(from, to); i += 1) {
            if (!setOne(boxes[i].value, box.checked)) { blocked = true; }
          }
          if (blocked) { limitHit(); }
        }
      }
      lastClicked = box;
    });

    results.addEventListener('change', function (event) {
      var target = event.target;
      if (target.matches && target.matches('.qa-check')) {
        if (!setOne(target.value, target.checked)) { target.checked = false; limitHit(); }
      } else if (target.matches && target.matches('[data-qa-all]')) {
        var blocked = false;
        checks().forEach(function (box) {
          if (!setOne(box.value, target.checked)) { blocked = true; }
        });
        if (blocked) { limitHit(); }
      } else {
        return;
      }
      writeSelection();
      sync();
    });

    results.addEventListener('click', function (event) {
      if (event.target.closest && event.target.closest('[data-qa-clear]')) {
        selected.clear();
        writeSelection();
        sync();
      }
    });

    // The remembered selection includes accounts that are not on this page, so
    // it is rebuilt into the form at submit time: ticked boxes on screen plus
    // one hidden input for each id ticked elsewhere.
    results.addEventListener('submit', function (event) {
      var form = event.target.closest ? event.target.closest('[data-qa-form]') : null;
      if (!form) { return; }
      if (selected.size === 0) {
        event.preventDefault();
        say('Tick at least one account first.');
        return;
      }
      Array.prototype.forEach.call(form.querySelectorAll('input[data-qa-extra]'), function (node) {
        node.parentNode.removeChild(node);
      });
      var onScreen = new Set(checks().filter(function (box) { return box.checked; }).map(function (box) { return box.value; }));
      selected.forEach(function (id) {
        if (onScreen.has(id)) { return; }
        var hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = 'ids';
        hidden.value = id;
        hidden.setAttribute('data-qa-extra', '1');
        form.appendChild(hidden);
      });
    });

    /* ---- live search: swap the results block, never reload the page ---- */
    function currentUrl() {
      var params = new URLSearchParams();
      new FormData(filters).forEach(function (value, key) {
        var text = String(value).trim();
        if (text !== '' && DEFAULTS[key] !== text) { params.append(key, text); }
      });
      var query = params.toString();
      return listUrl + (query ? '?' + query : '');
    }

    function load(url, scrollToTop) {
      requestId += 1;
      var mine = requestId;
      if (inflight) { inflight.abort(); }
      inflight = window.AbortController ? new AbortController() : null;
      results.setAttribute('aria-busy', 'true');
      var options = { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' };
      if (inflight) { options.signal = inflight.signal; }
      window.fetch(url, options).then(function (response) {
        // No marker header = not our partial (an expired session bounced us to
        // the login page, say). Navigate properly instead of pasting it in.
        if (!response.ok || response.headers.get('X-QA-Partial') !== '1') {
          window.location.assign(url);
          return null;
        }
        return response.text();
      }).then(function (html) {
        if (html === null || mine !== requestId) { return; }
        results.innerHTML = html;
        window.history.replaceState(null, '', url);
        sync();
        if (scrollToTop && results.scrollIntoView) { results.scrollIntoView({ block: 'start' }); }
      }).catch(function (error) {
        if (error && error.name === 'AbortError') { return; }
        window.location.assign(url);
      }).then(function () {
        if (mine === requestId) { results.removeAttribute('aria-busy'); }
      });
    }

    filters.addEventListener('submit', function (event) {
      event.preventDefault();
      window.clearTimeout(typingTimer);
      load(currentUrl(), false);
    });
    filters.addEventListener('input', function (event) {
      if (event.target.name !== 'q') { return; }
      window.clearTimeout(typingTimer);
      typingTimer = window.setTimeout(function () { load(currentUrl(), false); }, TYPING_DELAY_MS);
    });
    filters.addEventListener('change', function (event) {
      if (event.target.tagName === 'SELECT') { load(currentUrl(), false); }
    });
    filters.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && event.target.name === 'q' && event.target.value) {
        event.target.value = '';
        window.clearTimeout(typingTimer);
        load(currentUrl(), false);
      }
    });
    var reset = filters.querySelector('[data-qa-reset]');
    if (reset) {
      reset.addEventListener('click', function (event) {
        event.preventDefault();
        filters.querySelector('[name="q"]').value = '';
        Array.prototype.forEach.call(filters.querySelectorAll('select'), function (select) { select.selectedIndex = 0; });
        // "Per page" has a real default (50), not "first option".
        var per = filters.querySelector('select[name="per"]');
        if (per) { per.value = '50'; }
        load(listUrl, false);
      });
    }

    results.addEventListener('click', function (event) {
      var link = event.target.closest ? event.target.closest('.qa-pager a') : null;
      if (!link || event.metaKey || event.ctrlKey || event.shiftKey) { return; }
      event.preventDefault();
      load(link.href, true);
    });

    // Back/forward can restore a stale DOM; the remembered set is the truth.
    window.addEventListener('pageshow', function (event) {
      if (event.persisted) { selected = readSelection(); sync(); }
    });

    // Search-first: put the cursor in the box — but only with a mouse. A plain
    // `autofocus` also raises the on-screen keyboard over half of a phone.
    var box = document.getElementById('qa-q');
    if (box && window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      box.focus();
      try { box.setSelectionRange(box.value.length, box.value.length); } catch (e) { /* type=search edge cases */ }
    }

    sync();
  }

  /* ---------------------------------------------------------------- review */
  function initConfirm(form) {
    var phrase = (form.getAttribute('data-phrase') || '').toUpperCase();
    var minReason = parseInt(form.getAttribute('data-min-reason'), 10) || 5;
    var typed = form.querySelector('[data-qa-phrase]');
    var why = form.querySelector('[name="reason"]');
    var go = form.querySelector('[data-qa-go]');
    if (!typed || !why || !go) { return; }
    var label = go.getAttribute('data-label') || go.textContent;

    function ready() {
      var said = (typed.value || '').trim().replace(/\s+/g, ' ').toUpperCase();
      return said === phrase && (why.value || '').trim().length >= minReason;
    }
    function update() { go.disabled = !ready(); go.textContent = label; }

    typed.addEventListener('input', update);
    why.addEventListener('input', update);
    form.addEventListener('submit', function (event) {
      if (!ready()) { event.preventDefault(); return; }
      // One click, one delete: the second click of a double-click is ignored.
      go.disabled = true;
      go.textContent = 'Deleting…';
    });
    window.addEventListener('pageshow', update);
    update();
  }

  var listRoot = document.getElementById('qa-root');
  if (listRoot && document.getElementById('qa-results') && document.getElementById('qa-filters')) {
    initList(listRoot);
  }
  var confirmForm = document.querySelector('[data-qa-confirm-form]');
  if (confirmForm) { initConfirm(confirmForm); }
})();
