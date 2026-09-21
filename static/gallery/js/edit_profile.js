/* Edit Profile — live name-style preview, driven by the server's own style
   maps (#name-style-maps) so the preview always matches what saves. */
(function () {
  const mapsEl = document.getElementById('name-style-maps');
  const preview = document.getElementById('name-style-preview');
  if (!mapsEl || !preview) return;
  const maps = JSON.parse(mapsEl.textContent);
  const pick = (sel) => document.querySelector(sel);
  const personaSel = pick('[data-style="persona"]');
  const selectedPersona = () => (personaSel && personaSel.value) || 'classic';
  const refresh = () => {
    const font = maps.fonts[pick('[data-style="font"]').value] || '';
    const color = maps.colors[pick('[data-style="color"]').value] || '';
    const size = maps.sizes[pick('[data-style="size"]').value] || '';
    const fx = maps.fx[pick('[data-style="fx"]').value] || '';
    const rainbow = pick('[data-style="color"]').value === 'rainbow';
    const persona = (maps.personas && maps.personas[selectedPersona()]) || {};
    preview.setAttribute(
      'style',
      'font-size:18px;' +
        (font ? 'font-family:' + font + ';' : '') +
        (color ? 'color:' + color + ';' : ''),
    );
    preview.className = [
      'styled-name',
      rainbow ? 'namefx-rainbow' : '',
      size,
      fx,
      persona.cls || '',
    ]
      .filter(Boolean)
      .join(' ');
  };
  const applyPersona = (slug) => {
    const persona = maps.personas && maps.personas[slug];
    if (!persona) return;
    pick('[data-style="font"]').value = persona.font;
    pick('[data-style="color"]').value = persona.color;
    pick('[data-style="size"]').value = persona.size;
    pick('[data-style="fx"]').value = persona.fx;
    refresh();
  };
  const matchPersona = () => {
    const pack = {
      font: pick('[data-style="font"]').value,
      color: pick('[data-style="color"]').value,
      size: pick('[data-style="size"]').value,
      fx: pick('[data-style="fx"]').value,
    };
    let found = 'classic';
    Object.keys(maps.personas || {}).forEach((slug) => {
      if (slug === 'classic') return;
      const persona = maps.personas[slug];
      if (
        persona.font === pack.font &&
        persona.color === pack.color &&
        persona.size === pack.size &&
        persona.fx === pack.fx
      ) {
        found = slug;
      }
    });
    if (personaSel) personaSel.value = found;
    refresh();
  };
  if (personaSel) {
    personaSel.addEventListener('change', () => applyPersona(personaSel.value));
  }
  ['font', 'color', 'size', 'fx'].forEach((key) => {
    const el = pick('[data-style="' + key + '"]');
    if (el) el.addEventListener('change', matchPersona);
  });
})();

/* "Your websites" editor — new rows are cloned from the server-rendered
   empty form, so they validate exactly like the existing rows. */
(function () {
  const rowsBox = document.getElementById('link-rows');
  const addBtn = document.getElementById('add-link-btn');
  const tpl = document.getElementById('link-empty-template');
  const mgmt = document.getElementById('link-form-management');
  if (!rowsBox || !addBtn || !tpl || !mgmt) return;

  const TOTAL = mgmt.querySelector('[name$="-TOTAL_FORMS"]');
  const MAX = mgmt.querySelector('[name$="-MAX_NUM_FORMS"]');

  // The "New address" box only appears when the status is Moved.
  const syncMoved = (row) => {
    const sel = row.querySelector('[data-link-status]');
    const wrap = row.querySelector('[data-link-moved-to-wrap]');
    if (sel && wrap) wrap.hidden = sel.value !== 'moved';
  };

  const wire = (row) => {
    const sel = row.querySelector('[data-link-status]');
    if (sel) sel.addEventListener('change', () => syncMoved(row));
    const rm = row.querySelector('[data-link-remove]');
    if (rm) {
      rm.addEventListener('click', () => {
        // Saved row: tick its hidden DELETE. New row: blank it and drop it
        // from the DOM. TOTAL_FORMS must not shrink or the indexes shift.
        const del = row.querySelector('input[name$="-DELETE"]');
        if (del) {
          del.checked = true;
          row.style.display = 'none';
        } else {
          row.querySelectorAll('input[type="text"], input[type="url"]').forEach((i) => (i.value = ''));
          row.querySelectorAll('select').forEach((s) => (s.value = s.options[0].value));
          row.remove();
        }
        addBtn.disabled = false;
      });
    }
    syncMoved(row);
  };

  rowsBox.querySelectorAll('[data-link-row]').forEach(wire);

  addBtn.addEventListener('click', () => {
    const count = parseInt(TOTAL.value, 10) || 0;
    const max = parseInt(MAX && MAX.value, 10) || 12;
    if (count >= max) {
      addBtn.disabled = true;
      return;
    }
    // Swap __prefix__ for the next free index (names and ids).
    const holder = document.createElement('div');
    holder.innerHTML = tpl.innerHTML.split('__prefix__').join(String(count));
    const row = holder.firstElementChild;
    if (!row) return;
    rowsBox.appendChild(row);
    TOTAL.value = String(count + 1);
    if (count + 1 >= max) addBtn.disabled = true;
    wire(row);
    const firstInput = row.querySelector('input[type="text"]');
    if (firstInput) firstInput.focus();
  });
})();
