/* Pre-paint bootstrap — apply saved appearance and sidebar geometry before
   first paint. Shared by base.html, the standalone error pages, and the
   snippet preview blocked page. Must stay a blocking head script. */
try {
  var t = localStorage.getItem('blaq-theme');
  if (t !== 'light' && t !== 'dark') t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  var nw = parseInt(localStorage.getItem('blaq-nav-width'), 10);
  if (Number.isFinite(nw) && nw >= 180 && nw <= 380) document.documentElement.style.setProperty('--nav-w', nw + 'px');
  if (localStorage.getItem('blaq-nav-collapsed') === '1') document.documentElement.setAttribute('data-nav-collapsed', 'true');
  if (localStorage.getItem('blaq-fab-tip-dismissed') === '1') {
    document.documentElement.setAttribute('data-fab-tip-dismissed', 'true');
    if (document.cookie.indexOf('blaq_fab_tip_dismissed=1') === -1) {
      document.cookie = 'blaq_fab_tip_dismissed=1;path=/;max-age=31536000;SameSite=Lax';
    }
  }
} catch (e) {}
