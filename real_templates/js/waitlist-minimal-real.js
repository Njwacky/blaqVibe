/* Extracted from html/waitlist-minimal-real.html — the demo join handler
   (was an inline onsubmit attribute). The seeder ships it as js_code. */
document.getElementById('waitlist-join').addEventListener('submit', function (e) {
  e.preventDefault();
  alert('Joined!');
});
