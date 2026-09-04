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
