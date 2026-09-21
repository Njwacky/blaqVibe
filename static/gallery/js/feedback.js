/* Feedback screenshots: a small client-side preview; the server still
   validates the bytes, format, dimensions and size before saving anything. */
(function () {
  'use strict';

  const MAX_BYTES = 7 * 1024 * 1024;
  const allowed = new Set(['image/png', 'image/jpeg', 'image/webp']);

  function initAttachment(input) {
    const root = input.closest('[data-feedback-attachment]');
    const name = root && root.querySelector('[data-feedback-file-name]');
    const preview = root && root.parentElement.querySelector('[data-feedback-preview]');
    if (!root || !name) return;

    let objectUrl = '';
    input.addEventListener('change', function () {
      const file = input.files && input.files[0];
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = '';
      }
      if (preview) {
        preview.replaceChildren();
        preview.hidden = true;
      }
      name.classList.remove('has-file');

      if (!file) {
        name.textContent = 'No picture selected · PNG, JPG or WebP up to 7MB';
        return;
      }
      if (file.size > MAX_BYTES) {
        input.value = '';
        name.textContent = 'That picture is too large — maximum 7MB';
        return;
      }
      if (file.type && !allowed.has(file.type)) {
        input.value = '';
        name.textContent = 'Use a PNG, JPEG, or WebP screenshot';
        return;
      }

      name.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)}MB`;
      name.classList.add('has-file');
      if (!preview || !file.type.startsWith('image/')) return;

      objectUrl = URL.createObjectURL(file);
      const image = document.createElement('img');
      image.src = objectUrl;
      image.alt = 'Preview of the screenshot you will attach';
      preview.appendChild(image);
      preview.hidden = false;
    });
  }

  function initFabToggle() {
    const wrap = document.querySelector('[data-feedback-fab-setting]');
    const toggle = wrap && wrap.querySelector('.js-feedback-fab-toggle');
    if (!wrap || !toggle) return;
    const url = wrap.dataset.url || '';
    const csrf = wrap.dataset.csrf || '';
    toggle.addEventListener('change', function () {
      const value = toggle.checked;
      fetch(url, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf,
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: `key=show_feedback_fab&value=${value}`,
      })
        .then((response) => response.json())
        .then((data) => {
          if (!data.ok) throw new Error(data.error || 'Toggle failed');
          window.setTimeout(() => window.location.reload(), 250);
        })
        .catch(() => {
          toggle.checked = !value;
        });
    });
  }

  function init() {
    document.querySelectorAll('.js-feedback-attachment').forEach(initAttachment);
    initFabToggle();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
}());
