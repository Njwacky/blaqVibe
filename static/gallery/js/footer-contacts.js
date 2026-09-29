/* Extracted from: templates/users/footer_contacts.html
   Loaded by a <link>/<script> tag in those template(s); keep the two in sync. */
(function () {
  // The value box means something different per type ("082 555 0100" for a
  // WhatsApp number, "support@…" for email), so the placeholder and hint
  // follow the dropdown instead of guessing.
  var guide = {};
  try {
    guide = JSON.parse(document.getElementById('footer-kind-guide').textContent || '{}');
  } catch (err) {
    guide = {};
  }

  var rows = document.getElementById('footer-contact-rows');
  var addBtn = document.getElementById('footer-contact-add');
  var emptyRow = document.getElementById('footer-contact-empty-row');
  var totalInput = document.getElementById('id_form-TOTAL_FORMS');
  var counter = document.getElementById('footer-contact-count');
  var maxContacts = parseInt((rows && rows.getAttribute('data-max-contacts')) || '0', 10);

  function rowCount() {
    return rows ? rows.querySelectorAll('[data-contact-row]').length : 0;
  }

  function refreshCounter() {
    if (!counter) return;
    // Count what a save would keep: rows marked for removal are leaving, so
    // they should not argue with the "12 methods max" the server enforces.
    var kept = 0;
    var marked = 0;
    rows.querySelectorAll('[data-contact-row]').forEach(function (row) {
      var remove = row.querySelector('input[name$="-DELETE"]');
      if (remove && remove.checked) { marked += 1; } else { kept += 1; }
    });
    counter.textContent = kept + (kept === 1 ? ' method' : ' methods')
      + (marked ? ' · ' + marked + ' marked for removal' : '')
      + ' · ' + maxContacts + ' max';
    if (addBtn) addBtn.disabled = kept >= maxContacts;
  }

  function applyKind(row) {
    var select = row.querySelector('select[name$="-kind"]');
    var value = row.querySelector('input[name$="-value"]');
    var hint = row.querySelector('[data-contact-hint]');
    if (!select || !value) return;
    var meta = guide[select.value];
    if (meta && meta.placeholder) value.placeholder = meta.placeholder;
    if (hint) hint.textContent = meta && meta.hint ? meta.hint : '';
  }

  function nextOrder() {
    var max = 0;
    rows.querySelectorAll('input[name$="-position"]').forEach(function (input) {
      var parsed = parseInt(input.value, 10);
      if (!isNaN(parsed) && parsed > max) max = parsed;
    });
    return max + 10;
  }

  function watch(row) {
    var select = row.querySelector('select[name$="-kind"]');
    if (select) select.addEventListener('change', function () { applyKind(row); });
    var remove = row.querySelector('input[name$="-DELETE"]');
    if (remove) {
      // A row re-rendered with Remove ticked (a validation error sent the form
      // back) must look removed on arrival, not only once the box is toggled.
      row.classList.toggle('footer-contact-row--removed', remove.checked);
      remove.addEventListener('change', function () {
        row.classList.toggle('footer-contact-row--removed', remove.checked);
        refreshCounter();
      });
    }
    applyKind(row);
  }

  if (rows) {
    rows.querySelectorAll('[data-contact-row]').forEach(watch);
    refreshCounter();
  }

  if (addBtn && emptyRow && totalInput) {
    addBtn.addEventListener('click', function () {
      var index = parseInt(totalInput.value, 10) || 0;
      var wrapper = document.createElement('div');
      // Django's empty_form renders every name with __prefix__; swapping it
      // for the next index is all that is needed for the row to be saved.
      wrapper.innerHTML = emptyRow.innerHTML.replace(/__prefix__/g, String(index));
      var row = wrapper.firstElementChild;
      if (!row) return;
      var position = row.querySelector('input[name$="-position"]');
      if (position) position.value = String(nextOrder());
      rows.appendChild(row);
      totalInput.value = String(index + 1);
      watch(row);
      refreshCounter();
      var firstField = row.querySelector('select[name$="-kind"]');
      if (firstField) firstField.focus();
    });
  }
})();
