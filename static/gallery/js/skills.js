/* Copy a skill workflow to the clipboard.

   Delegated from the document so it works without waiting for DOMContentLoaded
   and survives the card being re-rendered. The click target is a
   data attribute rather than an inline onclick because the app runs a strict
   CSP — inline handlers are refused, and this file is loaded with a nonce.

   copyText()/toast() are the globals from blaqvibes.js; this only wires the
   button to them so the page does not need a second clipboard implementation.
*/
(function () {
  document.addEventListener('click', function (event) {
    var btn = event.target.closest && event.target.closest('[data-copy-target]');
    if (!btn) return;
    var selector = btn.getAttribute('data-copy-target');
    var source = selector && document.querySelector(selector);
    if (!source) return;
    if (typeof copyText !== 'function') return;
    copyText(source.textContent);
  });
})();
