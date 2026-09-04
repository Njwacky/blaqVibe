function goStep(n) {
  document.querySelectorAll('.wizard-step').forEach((el) => { el.style.display = 'none'; });
  document.querySelector('[data-step="' + n + '"]').style.display = 'block';
  document.querySelectorAll('.step').forEach((el) => {
    const step = parseInt(el.dataset.step, 10);
    el.classList.toggle('on', step === n);
    el.style.opacity = step <= n ? '1' : '.5';
    const ind = document.getElementById('step-ind-' + step);
    if (ind) ind.classList.toggle('on', step === n);
  });
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
if (form) {
  form.addEventListener('submit', function (e) {
    if (zipInput && zipInput.files[0] && !checkZip(zipInput.files[0])) {
      e.preventDefault();
      goStep(2);
      return;
    }
    if (!window.FormData || !window.XMLHttpRequest) return;
    e.preventDefault();
    const barWrap = document.getElementById('zip-progress');
    const bar = document.getElementById('zip-bar');
    if (barWrap) barWrap.style.display = 'block';
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = function (ev) {
      if (ev.lengthComputable && bar) bar.style.width = Math.round((ev.loaded / ev.total) * 100) + '%';
    };
    xhr.onload = function () {
      if (xhr.responseURL) { window.location.href = xhr.responseURL; return; }
      document.open(); document.write(xhr.responseText); document.close();
    };
    xhr.onerror = function () { setHint('Upload failed. Try again.', true); };
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
document.querySelectorAll('[data-wizard-step]').forEach((btn) => {
  btn.addEventListener('click', () => goStep(parseInt(btn.dataset.wizardStep, 10)));
});
document.querySelectorAll('[data-wizard-action="start-again"]').forEach((btn) => {
  btn.addEventListener('click', startAgain);
});
