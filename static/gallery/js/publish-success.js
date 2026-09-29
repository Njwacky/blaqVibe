/* Extracted from: templates/gallery/publish_success.html
   Loaded by a <link>/<script> tag in those template(s); keep the two in sync. */
// The first-win page deliberately has no polling loop. Pending ZIP uploads
// already have the existing scan status page/notification path; keeping this
// page static avoids turning a celebratory screen into another network request.
//
// Copy-link buttons on the success page (detail.js is not loaded here).
(function () {
  var toastEl = document.getElementById('toast');
  function toast() {
    if (!toastEl) return;
    toastEl.classList.add('show');
    setTimeout(function () { toastEl.classList.remove('show'); }, 1600);
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.js-copy');
    if (!btn) return;
    var text = btn.getAttribute('data-copy') || '';
    var done = function () { toast(); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () {});
    } else {
      var ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (err) {}
      document.body.removeChild(ta); done();
    }
  });
})();
