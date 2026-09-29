/* Extracted from: templates/gallery/import_repo.html
   Loaded by a <link>/<script> tag in those template(s); keep the two in sync. */
(function(){
  var btn = document.querySelector('.js-fill-demo');
  var input = document.getElementById('repo_url');
  if (!btn || !input) return;
  btn.addEventListener('click', function(){
    input.value = btn.getAttribute('data-url') || '';
    input.focus();
  });
})();
