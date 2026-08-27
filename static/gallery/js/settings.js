/* Settings — one-tap toggles. The toggle endpoint URL + CSRF token are
   server-rendered into data attributes on #settings-toggles (this file
   carries no template tags). */
(function () {
  const wrap = document.getElementById('settings-toggles');
  const apiUrl = (wrap && wrap.dataset.url) || '';
  const csrfToken = (wrap && wrap.dataset.csrf) || '';
  document.querySelectorAll('.switch input').forEach((el) => {
    el.addEventListener('change', () => {
      const key = el.dataset.key;
      const value = el.checked;
      fetch(apiUrl, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken,
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: `key=${encodeURIComponent(key)}&value=${value}`,
      })
        .then((r) => r.json())
        .then((d) => {
          if (d.ok) {
            try {
              toast((value ? 'Enabled ' : 'Disabled ') + key);
            } catch (e) {}
          } else {
            el.checked = !value;
            try {
              toast('Failed: ' + d.error);
            } catch (e) {}
          }
        })
        .catch(() => {
          el.checked = !value;
        });
    });
  });

  // (The name-style live preview moved to Edit Profile with the picker —
  // nothing on this page references #name-style-maps any more.)
})();
