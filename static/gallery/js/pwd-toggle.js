/* BlaqVibes — show/hide password toggle (eye icon)
   Auto-wraps every <input type="password"> on the page with a toggle button.
   No dependencies; safe to include on any auth page. */
(function () {
  const EYE_OPEN =
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/>' +
    '<circle cx="12" cy="12" r="3"/></svg>';
  const EYE_CLOSED =
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>' +
    '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>' +
    '<path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/>' +
    '<line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function init() {
    document.querySelectorAll('input[type="password"]').forEach(function (input) {
      // Avoid double-init if the script loads twice
      if (input.parentElement && input.parentElement.classList.contains('pwd-field')) return;

      var wrap = document.createElement('div');
      wrap.className = 'pwd-field';
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pwd-toggle';
      btn.setAttribute('aria-label', 'Show password');
      btn.setAttribute('title', 'Show password');
      btn.innerHTML = EYE_OPEN;

      btn.addEventListener('click', function () {
        var isPassword = input.type === 'password';
        input.type = isPassword ? 'text' : 'password';
        btn.innerHTML = isPassword ? EYE_CLOSED : EYE_OPEN;
        btn.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
        btn.setAttribute('title', isPassword ? 'Hide password' : 'Show password');
        input.focus();
      });

      wrap.appendChild(btn);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
