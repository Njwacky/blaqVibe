try {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(() => console.log('SW ok')).catch(() => {});
  }
} catch (e) {}
let deferredPrompt = null;
const banner = document.getElementById('pwa-install-banner');
const installBtn = document.getElementById('pwa-install-btn');
const dismissBtn = document.getElementById('pwa-dismiss-btn');
const iosHint = document.getElementById('pwa-ios-hint');
if (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone) {
} else {
  window.addEventListener('beforeinstallprompt', (e) => {
    try {
      e.preventDefault();
      deferredPrompt = e;
      setTimeout(() => {
        if (!localStorage.getItem('pwa-dismissed')) {
          banner.style.display = 'flex';
        }
      }, 2000);
    } catch (err) {}
  });
  const isIos = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (isIos && !localStorage.getItem('pwa-ios-dismissed')) {
    setTimeout(() => { iosHint.style.display = 'block'; }, 3000);
  }
  window.addEventListener('appinstalled', () => {
    try { banner.style.display = 'none'; iosHint.style.display = 'none'; localStorage.removeItem('pwa-dismissed'); } catch (e) {}
    try { toast('✓ BlaqVibes installed!'); } catch (e) {}
  });
}
if (installBtn) {
  installBtn.addEventListener('click', async () => {
    try {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      if (choice.outcome === 'accepted') { try { toast('Installing BlaqVibes...'); } catch (e) {} }
      deferredPrompt = null;
      banner.style.display = 'none';
    } catch (e) {}
  });
}
if (dismissBtn) {
  dismissBtn.addEventListener('click', () => {
    try { localStorage.setItem('pwa-dismissed', '1'); } catch (e) {}
    banner.style.display = 'none';
    setTimeout(() => { try { localStorage.removeItem('pwa-dismissed'); } catch (e) {} }, 7 * 24 * 60 * 60 * 1000);
  });
}
if (iosHint) {
  iosHint.querySelector('button').addEventListener('click', () => {
    try { localStorage.setItem('pwa-ios-dismissed', '1'); } catch (e) {}
  });
}
