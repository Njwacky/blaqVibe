/* Publish — one lightweight form. ZIP validation/drag-drop and the XHR
   upload progress bar. No wizard: publishing is a single step, and the
   strengthen-your-proof work happens after the project is already out.
   No template tags inside: everything reads from the DOM. */
const MAX_ZIP = 100 * 1024 * 1024;
const ZIP_ACCEPT = '.zip,application/zip,application/x-zip-compressed,application/x-zip';
const zipInput = document.querySelector('#id_zip_file');
const drop = document.getElementById('zip-drop');

function setHint(msg, bad) {
  // Re-query every time: after an in-place re-render (see swapDocument below)
  // a captured node belongs to the old document and writing to it is invisible.
  const el = document.getElementById('zip-hint');
  if (el) { el.textContent = msg; el.style.color = bad ? '#F87171' : 'var(--muted)'; }
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

function resetSubmit() {
  const barWrap = document.getElementById('zip-progress');
  if (barWrap) barWrap.style.display = 'none';
  const btn = form.querySelector('.pub-submit');
  if (btn) { btn.disabled = false; btn.textContent = 'Publish project'; }
}

/* Put the server's own answer on screen without navigating.

   This is the whole fix for "I filled everything in, pressed Publish and it
   just came back to the same page with no error". The server answers a bad
   submission IN PLACE: 200 with the form re-rendered and its errors attached
   (or a 429 upload-limit page). The XHR follows redirects, so on success
   `responseURL` is /publish/done/… and differs from this page — but on a
   rejected submission it is /publish/ again, identical to where we are.
   Assigning that back to window.location reloaded a brand-new, empty form:
   the reason for the rejection was downloaded and then thrown away, along
   with everything the builder had typed. */
function swapDocument(html, files) {
  document.open();
  document.write(html);
  document.close();

  // A re-rendered form can never carry the uploaded file back — browsers do
  // not put a File in an HTML value. We still hold the File objects, so put
  // them back: a 100MB ZIP must not have to be picked twice over mobile data.
  const newInput = document.getElementById('id_zip_file');
  if (newInput && files.length && window.DataTransfer) {
    try {
      // Same reference as the guard above — never a bare global that the
      // guard did not just prove exists.
      const dt = new window.DataTransfer();
      files.forEach((f) => dt.items.add(f));
      newInput.files = dt.files;
      const title = document.querySelector('.zip-picker__title');
      if (title) title.textContent = files[0].name;
      const lostNote = document.getElementById('zip-lost-note');
      if (lostNote) lostNote.remove();
      setHint(files[0].name + ' — still attached. Fix the fields above and publish again.', false);
    } catch (err) {
      // Never a silent failure again: say it on the page and in the console.
      setHint('Fix the fields above, then pick your ZIP again before publishing.', true);
      if (window.console && window.console.warn) window.console.warn('publish: could not re-attach the ZIP', err);
    }
  }

  // Land on the reason, not at the top of a long form. Best-effort only: the
  // swap above is the part that matters, so a missing focus/scroll API in an
  // old webview must never be able to undo it.
  try {
    const summary = document.getElementById('publish-errors');
    if (summary) {
      summary.setAttribute('tabindex', '-1');
      summary.focus({ preventScroll: true });
      if (summary.scrollIntoView) summary.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  } catch (err) { /* the error is already on screen; do not throw over it */ }
}

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
    const here = window.location.href;
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = function (ev) {
      if (ev.lengthComputable && bar) bar.style.width = Math.round((ev.loaded / ev.total) * 100) + '%';
    };
    xhr.onload = function () {
      // Moved somewhere else = the publish landed (302 → publish/done/…).
      // Answered here = the server is telling us something; show it.
      const movedAway = xhr.responseURL && xhr.responseURL !== here;
      if (movedAway) { window.location.href = xhr.responseURL; return; }
      if (xhr.status >= 400 && !/^\s*</.test(xhr.responseText || '')) {
        // Nothing to render (dropped connection, proxy error, oversized body):
        // say so on this page instead of blanking it or faking a success.
        setHint('Upload failed (' + xhr.status + '). Nothing was published — check your connection and try again.', true);
        resetSubmit();
        return;
      }
      const files = (zipInput && zipInput.files) ? Array.from(zipInput.files) : [];
      resetSubmit();
      swapDocument(xhr.responseText, files);
    };
    xhr.onerror = function () {
      setHint('Upload failed. Nothing was published — try again.', true);
      resetSubmit();
    };
    xhr.ontimeout = function () {
      setHint('The upload timed out. Nothing was published — try again.', true);
      resetSubmit();
    };
    xhr.open('POST', form.action || window.location.href);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.send(new FormData(form));
  });
  // A back/forward-cache restore can bring this page back with the submit
  // button still locked from the previous attempt ("Publishing…"). Unlock it:
  // the server now de-dupes the resubmit by token, so trying again is safe.
  window.addEventListener('pageshow', function () {
    const btn = form.querySelector('.pub-submit');
    if (btn) { btn.disabled = false; btn.textContent = 'Publish project'; }
  });
}
