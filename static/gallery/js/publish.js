/* Publish — one lightweight form. ZIP validation/drag-drop and the XHR
   upload progress bar. No wizard: publishing is a single step, and the
   strengthen-your-proof work happens after the project is already out.
   No template tags inside: everything reads from the DOM. */
const MAX_ZIP = 100 * 1024 * 1024;
const ZIP_ACCEPT = '.zip,application/zip,application/x-zip-compressed,application/x-zip';
const zipInput = document.querySelector('#id_zip_file');
const drop = document.getElementById('zip-drop');
const hint = document.getElementById('zip-hint');

function setHint(msg, bad) {
  if (hint) { hint.textContent = msg; hint.style.color = bad ? '#F87171' : 'var(--muted)'; }
}

function checkZip(file) {
  if (!file) return true;
  if (!file.name.toLowerCase().endsWith('.zip')) { setHint('Only .zip files. Photos and folders are not a ZIP — zip the project first, then pick it from Files.', true); return false; }
  if (file.size > MAX_ZIP) { setHint('ZIP is over 100MB.', true); return false; }
  setHint(file.name + ' — ' + Math.round(file.size / 1024) + ' KB', false);
  return true;
}

function tuneZipAccept(allFiles) {
  if (!zipInput) return;
  // image/* (or no accept) is what sends phones to Pictures. A zip accept
  // list asks the OS for Documents/Files. Android sometimes hides .zip
  // behind application/octet-stream, so "browse all" widens the filter
  // without going back to the gallery.
  zipInput.setAttribute('accept', allFiles
    ? '.zip,application/zip,application/x-zip-compressed,application/octet-stream'
    : ZIP_ACCEPT);
}

if (zipInput) {
  tuneZipAccept(false);
  zipInput.addEventListener('change', () => {
    const file = zipInput.files[0];
    checkZip(file);
    const title = document.querySelector('.zip-picker__title');
    if (title && file) title.textContent = file.name;
  });
}

document.querySelectorAll('[data-zip-browse-all]').forEach((btn) => {
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    tuneZipAccept(true);
    if (zipInput) zipInput.click();
  });
});

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

// One submit path: validate the ZIP choice, then upload over XHR so a big
// archive shows real progress instead of a dead spinner.
const form = document.getElementById('publish-form');
if (form) {
  form.addEventListener('submit', function (e) {
    if (zipInput && zipInput.files[0] && !checkZip(zipInput.files[0])) {
      e.preventDefault();
      if (drop) drop.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    if (!window.FormData || !window.XMLHttpRequest) return;
    e.preventDefault();
    const barWrap = document.getElementById('zip-progress');
    const bar = document.getElementById('zip-bar');
    if (barWrap) barWrap.style.display = 'block';
    const btn = form.querySelector('.pub-submit');
    if (btn) { btn.disabled = true; btn.textContent = 'Publishing…'; }
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = function (ev) {
      if (ev.lengthComputable && bar) bar.style.width = Math.round((ev.loaded / ev.total) * 100) + '%';
    };
    xhr.onload = function () {
      if (xhr.responseURL) { window.location.href = xhr.responseURL; return; }
      document.open(); document.write(xhr.responseText); document.close();
    };
    xhr.onerror = function () {
      setHint('Upload failed. Try again.', true);
      if (barWrap) barWrap.style.display = 'none';
      if (btn) { btn.disabled = false; btn.textContent = 'Publish project'; }
    };
    xhr.open('POST', form.action || window.location.href);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.send(new FormData(form));
  });
}
