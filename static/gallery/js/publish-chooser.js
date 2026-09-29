/* Extracted from: templates/gallery/base.html
   Loaded by a <link>/<script> tag in those template(s); keep the two in sync. */
(function(){
  var backdrop = document.getElementById('pub-choose');
  if(!backdrop) return;
  var uploadBtn = document.getElementById('pub-choose-upload');
  var closeBtn = document.getElementById('pub-choose-close');
  var lastFocus = null;
  function openChooser(uploadHref){
    lastFocus = document.activeElement;
    if(uploadHref) uploadBtn.setAttribute('href', uploadHref);
    backdrop.hidden = false;
    backdrop.classList.add('open');
    var menu = document.getElementById('nav-menu');
    if(menu) menu.classList.remove('open');
    var links = document.getElementById('nav-links');
    if(links) links.classList.remove('open');
    if(closeBtn) closeBtn.focus();
    document.body.style.overflow = 'hidden';
  }
  function closeChooser(){
    backdrop.classList.remove('open');
    backdrop.hidden = true;
    document.body.style.overflow = '';
    if(lastFocus && lastFocus.focus) lastFocus.focus();
  }
  document.addEventListener('click', function(e){
    if(e.defaultPrevented) return;
    if(e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var t = e.target;
    if(t && t.closest){
      if(t.closest('#pub-choose-close')){ e.preventDefault(); closeChooser(); return; }
      if(t === backdrop){ closeChooser(); return; }
      var link = t.closest('a[href]');
      if(link && !link.hasAttribute('data-no-chooser')){
        var raw = link.getAttribute('href') || '';
        var path = raw.split('?')[0].split('#')[0];
        var isWelcomeHandoff = /(?:^|[?&])welcome=1(?:&|$)/.test(raw.split('#')[0]);
        if((path === '/publish' || path === '/publish/') && !isWelcomeHandoff){
          e.preventDefault();
          e.stopPropagation();
          openChooser(link.href);
          return;
        }
      }
    }
  }, true);
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape' && backdrop.classList.contains('open')) closeChooser();
  });
})();
