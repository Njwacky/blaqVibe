/* Publish wizard — step navigation, ZIP validation/drag-drop, README
   preview, and the XHR upload progress bar. No template tags inside:
   everything reads from the DOM. */
function formatBytes(n) {
  n = Number(n) || 0;
  const raw = n.toLocaleString() + ' bytes';
  if (n < 1024) return raw;
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB (' + raw + ')';
  return (n / (1024 * 1024)).toFixed(2) + ' MB (' + raw + ')';
}
function fillConfirm() {
  const title = document.querySelector('#id_title');
  const desc = document.querySelector('#id_short_description');
  const cTitle = document.getElementById('c-title');
  const cDesc = document.getElementById('c-desc');
  const cMeta = document.getElementById('c-meta');
  if (cTitle) cTitle.textContent = (title && title.value.trim()) || 'Untitled vibe';
  if (cDesc) cDesc.textContent = (desc && desc.value.trim()) || '';
  const bits = [];
  const file = zipInput && zipInput.files && zipInput.files[0];
  if (file) {
    bits.push(file.name);
    bits.push(formatBytes(file.size));
  } else {
    const html = document.querySelector('#id_html_code');
    const css = document.querySelector('#id_css_code');
    const js = document.querySelector('#id_js_code');
    const size = [html, css, js].reduce((sum, el) => sum + (el && el.value ? new Blob([el.value]).size : 0), 0);
    bits.push('HTML snippet');
    bits.push(formatBytes(size));
  }
  const cat = document.querySelector('#id_category');
  if (cat && cat.options && cat.selectedIndex >= 0) bits.push(cat.options[cat.selectedIndex].text);
  if (cMeta) cMeta.textContent = bits.join(' · ');
}
function goStep(n) {
  document.querySelectorAll('.wizard-step').forEach((el) => { el.style.display = 'none'; });
  document.querySelector('[data-step="' + n + '"]').style.display = 'block';
  if (n === 3) fillConfirm();
  document.querySelectorAll('.step').forEach((el) => {
    const step = parseInt(el.dataset.step, 10);
    el.classList.toggle('on', step === n);
    el.style.opacity = step <= n ? '1' : '.5';
    const ind = document.getElementById('step-ind-' + step);
    if (ind) ind.classList.toggle('on', step === n);
  });
  // Update progress bars
  document.getElementById('bar-1').style.background = n >= 2 ? '#7C3AED' : 'var(--line)';
  document.getElementById('bar-2').style.background = n >= 3 ? '#7C3AED' : 'var(--line)';
  document.getElementById('step-ind-2').style.opacity = n >= 2 ? '1' : '.5';
  document.getElementById('step-ind-3').style.opacity = n >= 3 ? '1' : '.5';
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
const MAX_ZIP = 100 * 1024 * 1024;
const zipInput = document.querySelector('#id_zip_file');
const drop = document.getElementById('zip-drop');
const hint = document.getElementById('zip-hint');
const readme = document.querySelector('#id_readme');
const preview = document.getElementById('readme-preview');
function setHint(msg, bad) {
  if (hint) { hint.textContent = msg; hint.style.color = bad ? '#F87171' : 'var(--muted)'; }
}
function checkZip(file) {
  if (!file) return true;
  if (!file.name.toLowerCase().endsWith('.zip')) { setHint('Only .zip files.', true); return false; }
  if (file.size > MAX_ZIP) { setHint('ZIP is over 100MB.', true); return false; }
  setHint(file.name + ' — ' + Math.round(file.size / 1024) + ' KB', false);
  return true;
}
if (zipInput) {
  zipInput.addEventListener('change', () => checkZip(zipInput.files[0]));
}
if (drop && zipInput) {
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = '#7C3AED'; }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = 'var(--line)'; }));
  drop.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if (!file) return;
    const dt = new DataTransfer();
    dt.items.add(file);
    zipInput.files = dt.files;
    checkZip(file);
  });
}
if (readme && preview) {
  const paint = () => {
    const v = readme.value || '';
    preview.style.display = v.trim() ? 'block' : 'none';
    preview.textContent = v.slice(0, 800);
  };
  readme.addEventListener('input', paint);
  paint();
}
const form = document.getElementById('wizard-form');
const wait = document.getElementById('upload-wait');
const waitFile = document.getElementById('upload-wait-file');
const waitBytes = document.getElementById('upload-wait-bytes');
const waitFill = document.getElementById('upload-wait-fill');
function snippetBytes() {
  const html = document.querySelector('#id_html_code');
  const css = document.querySelector('#id_css_code');
  const js = document.querySelector('#id_js_code');
  return [html, css, js].reduce((sum, el) => sum + (el && el.value ? new Blob([el.value]).size : 0), 0);
}
function showWait() {
  if (!wait) return;
  const file = zipInput && zipInput.files && zipInput.files[0];
  if (waitFile) waitFile.textContent = file ? file.name : 'HTML snippet';
  if (waitBytes) waitBytes.textContent = formatBytes(file ? file.size : snippetBytes());
  if (waitFill) waitFill.style.width = '0%';
  wait.hidden = false;
}
function hideWait() {
  if (wait) wait.hidden = true;
}
if (form) {
  form.addEventListener('submit', function (e) {
    if (zipInput && zipInput.files[0] && !checkZip(zipInput.files[0])) {
      e.preventDefault();
      hideWait();
      goStep(2);
      return;
    }
    if (!window.FormData || !window.XMLHttpRequest) return;
    e.preventDefault();
    showWait();
    const barWrap = document.getElementById('zip-progress');
    const bar = document.getElementById('zip-bar');
    if (barWrap) barWrap.style.display = 'block';
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = function (ev) {
      if (!ev.lengthComputable) return;
      const pct = Math.round((ev.loaded / ev.total) * 100) + '%';
      if (bar) bar.style.width = pct;
      if (waitFill) waitFill.style.width = pct;
    };
    xhr.onload = function () {
      if (xhr.status >= 400) {
        hideWait();
        setHint('Upload failed. Try again.', true);
        return;
      }
      if (xhr.responseURL) { window.location.href = xhr.responseURL; return; }
      document.open(); document.write(xhr.responseText); document.close();
    };
    xhr.onerror = function () { hideWait(); setHint('Upload failed. Try again.', true); };
    xhr.open('POST', form.action || window.location.href);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.send(new FormData(form));
  });
}
function startAgain() {
  if (confirm('Start again? This will clear the form.')) {
    document.getElementById('wizard-form').reset();
    goStep(1);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}
// Wizard nav buttons — no inline JS in the template; wire data attributes here.
document.querySelectorAll('[data-wizard-step]').forEach((btn) => {
  btn.addEventListener('click', () => goStep(parseInt(btn.dataset.wizardStep, 10)));
});
document.querySelectorAll('[data-wizard-action="start-again"]').forEach((btn) => {
  btn.addEventListener('click', startAgain);
});
