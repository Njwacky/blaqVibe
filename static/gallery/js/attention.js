/* ============================================================================
   ATTENTION — the 30-minute re-nag, in the browser.

   The server nudges on a Celery beat (gallery.tasks.attention_reminders) and
   the banner is rendered on every page load. That covers somebody who browses.
   It does NOT cover the case this file exists for: a builder who leaves a tab
   open all day and never reloads — exactly the person a countdown is for.

   So: poll /attention/status/ on the reminder cadence and put the banner back
   when a reminder has landed. Three rules shape it:

   1. "Not now" is honoured for the tab, but only until the next reminder. A
      snooze that survives the cadence would be a way to silence a deadline,
      and the deadline ends in a deletion.
   2. The poll carries counts and clocks only — no titles, no slugs. It is
      unauthenticated-looking JSON on an authenticated endpoint, so it is
      written as if somebody hostile could read every byte of it.
   3. No polling at all when there is nothing open. A tab that nags nobody
      should cost nobody a request every 30 minutes.
   ========================================================================= */
(function () {
  'use strict';

  var SNOOZE_KEY = 'bv-attention-snoozed-at';
  var banner = document.querySelector('[data-attention-banner]');

  /* --- "Keep my pick" needs a pick -------------------------------------
     Disabled here rather than in the template: without JS the button still
     posts and the server refuses politely, which beats a button nobody can
     ever press. */
  function wirePickButtons() {
    var buttons = document.querySelectorAll('[data-needs-pick]');
    Array.prototype.forEach.call(buttons, function (button) {
      var form = document.getElementById(button.getAttribute('data-needs-pick'));
      if (!form) { return; }
      function sync() {
        var picked = form.querySelectorAll('input[name="project_id"]:checked');
        button.disabled = picked.length === 0;
      }
      // The radios live OUTSIDE the form (each sits in its own copy card) and
      // are bound to it with the HTML5 form= attribute, so listen on the
      // document rather than on the form.
      document.addEventListener('change', function (event) {
        if (event.target && event.target.name === 'project_id' &&
            event.target.getAttribute('form') === form.id) { sync(); }
      });
      button.disabled = true;
      sync();
    });
  }

  function readSnooze() {
    try { return parseInt(window.sessionStorage.getItem(SNOOZE_KEY) || '0', 10) || 0; }
    catch (e) { return 0; }
  }

  function writeSnooze(ts) {
    try {
      if (ts) { window.sessionStorage.setItem(SNOOZE_KEY, String(ts)); }
      else { window.sessionStorage.removeItem(SNOOZE_KEY); }
    } catch (e) { /* private mode: the banner just stays, which is the safe failure */ }
  }

  function show() { if (banner) { banner.style.display = ''; } }
  function hide() { if (banner) { banner.style.display = 'none'; } }

  function wireSnooze() {
    if (!banner) { return; }
    var button = banner.querySelector('[data-attention-snooze]');
    if (!button) { return; }
    button.addEventListener('click', function () {
      writeSnooze(Date.now());
      hide();
    });
  }

  function statusUrl() {
    if (banner) { return banner.getAttribute('data-attention-status-url'); }
    return '/attention/status/';
  }

  function refresh() {
    var url = statusUrl();
    if (!url) { return; }
    fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (!data || !data.ok) { return; }
        var nothingLeft = !data.open && !data.awaiting_delete;
        if (nothingLeft) { hide(); return; }
        if (!banner) { return; }
        // The badge in the nav is the same fact as the banner; keep them from
        // disagreeing inside one tab.
        var badges = document.querySelectorAll('.nav-badge[data-attention-unread]');
        Array.prototype.forEach.call(badges, function (badge) {
          badge.textContent = String(data.unread || 0);
          badge.style.display = data.unread ? '' : 'none';
        });
        // A reminder has landed since the last look → the snooze is over.
        if (data.due_reminder) {
          writeSnooze(0);
          show();
          banner.setAttribute('data-attention-open', String(data.open || 0));
          banner.setAttribute('data-attention-critical', String(data.critical || 0));
          try {
            if (typeof window.toast === 'function') {
              window.toast(data.open + ' decision' + (data.open === 1 ? '' : 's') + ' still waiting');
            }
          } catch (e) { /* toast is decoration; the banner is the interface */ }
        } else if (readSnooze()) {
          hide();
        } else {
          show();
        }
      })
      .catch(function () { /* offline: the next tick tries again */ });
  }

  function start() {
    wirePickButtons();
    wireSnooze();
    if (readSnooze()) { hide(); }
    if (!banner) { return; }  // nothing open → no polling, no cost

    var seconds = parseInt(banner.getAttribute('data-attention-reminder-seconds') || '0', 10);
    var every = (seconds > 0 ? seconds : 1800) * 1000;
    var timer = window.setInterval(refresh, every);

    // Background tabs get their timers throttled, which is exactly when a
    // builder leaves one open all day. Re-check the moment they come back.
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') { refresh(); }
    });
    window.addEventListener('pagehide', function () { window.clearInterval(timer); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
