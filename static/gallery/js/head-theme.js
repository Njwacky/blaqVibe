try {
  var t = localStorage.getItem('blaq-theme');
  if (t !== 'light' && t !== 'dark') t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  var nw = parseInt(localStorage.getItem('blaq-nav-width'), 10);
  if (Number.isFinite(nw) && nw >= 180 && nw <= 380) document.documentElement.style.setProperty('--nav-w', nw + 'px');
  if (localStorage.getItem('blaq-nav-collapsed') === '1') document.documentElement.setAttribute('data-nav-collapsed', 'true');
} catch (e) {}
