// BlaqVibes SW — static and HTML requests are network-first so installed PWAs
// receive layout fixes immediately while retaining an offline fallback.
// No secrets in JS — no S3 keys, no scan_report.
// Bump this whenever the app shell changes; activation removes older caches.
const CACHE = 'blaqvibes-v5-footer-contact';
// Account-specific pages — never cache (they contain per-user data).
const PRIVATE_PREFIXES = [
  '/my-vibes/', '/inbox/', '/saved/', '/settings/', '/payout/',
  '/trades/', '/moderation/', '/blaq-admin', '/admin/',
];
const STATIC_ASSETS = [
  '/static/gallery/css/blaqvibes.css?v=filter-tidy-20260828',
  '/static/gallery/css/footer.css?v=footer-contact-20260828',
  '/static/gallery/css/error.css?v=theme-20260815',
  '/static/gallery/css/forms.css?v=theme-colors-20260815',
  '/static/gallery/js/blaqvibes.js?v=theme-colors-20260815',
  '/static/gallery/js/detail.js?v=theme-colors-20260815',
  '/static/gallery/js/profile.js?v=theme-colors-20260815',
  '/static/branding/icon-192.png',
  '/static/branding/icon-512.png',
  '/static/branding/error-fork.png',
  '/static/branding/manifest.json'
];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC_ASSETS)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  // Only handle same-origin GET
  if(req.method !== 'GET' || url.origin !== location.origin) return;
  // Static -> network first. Django/WhiteNoise fingerprints production
  // assets, but local and previously installed PWAs can request stable URLs.
  // Cache-first kept an obsolete top navbar forever after the sidebar shipped.
  if(url.pathname.startsWith('/static/')){
    e.respondWith((async () => {
      try {
        const res = await fetch(req);
        if(res.ok){
          const cache = await caches.open(CACHE);
          await cache.put(req, res.clone());
        }
        return res;
      } catch(err) {
        return (await caches.match(req)) ||
          (await caches.match('/static/branding/error-fork.png')) ||
          Response.error();
      }
    })());
    return;
  }
  // HTML -> network first, fallback to cache, then safe page
  if(req.headers.get('accept')?.includes('text/html')){
    // Never cache account-specific pages — they contain per-user data.
    if(PRIVATE_PREFIXES.some(p => url.pathname.startsWith(p))){
      e.respondWith(fetch(req));
      return;
    }
    e.respondWith(fetch(req).then(res=>{
      // Don't cache 404/403 as success — but cache 200
      if(res.ok) { const clone=res.clone(); caches.open(CACHE).then(c=>c.put(req, clone)); }
      return res;
    }).catch(()=>caches.match(req).then(r=>r || caches.match('/oops/').then(r2=>r2 || Response.error()))));
    return;
  }
});
