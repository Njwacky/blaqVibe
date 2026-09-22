// BlaqVibes SW — static and HTML requests are network-first so installed PWAs
// receive layout fixes immediately while retaining an offline fallback.
// No secrets in JS — no S3 keys, no scan_report.
// Bump this whenever the app shell changes; activation removes older caches.
const CACHE = 'blaqvibes-v9-network-opt';
// Account-specific pages — never cache (they contain per-user data).
const PRIVATE_PREFIXES = [
  '/my-vibes/', '/inbox/', '/saved/', '/settings/', '/sales/',
  '/trades/', '/moderation/', '/blaq-admin', '/admin/',
];
// Keep these query strings in step with the ?v= tags in templates — a stale
// entry warms the offline cache with a stylesheet the pages no longer ask for.
const STATIC_ASSETS = [
  '/static/gallery/css/fonts.css?v=fonts-20260830',
  '/static/gallery/css/blaqvibes.css?v=fab-tip-dismiss-20260921',
  '/static/gallery/css/cards.css?v=mobile-20260906b',
  '/static/gallery/css/premium.css?v=product-system-20260909',
  '/static/gallery/css/footer.css?v=footer-polish-20260921',
  '/static/gallery/css/attention.css?v=attention-20260917',
  '/static/gallery/js/head-theme.js?v=fab-tip-dismiss-20260921',
  '/static/gallery/js/blaqvibes.js?v=fab-tip-dismiss-20260921',
  '/static/gallery/js/attention.js?v=attention-20260917',
  '/static/gallery/js/alpine.min.js?v=alpine-3.16.3',
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
  // Static -> stale-while-revalidate for CSS/JS (instant, background update), network-first for images
  if(url.pathname.startsWith('/static/')){
    // For CSS/JS: serve cache immediately, update in background — cuts TTFB
    if(url.pathname.match(/\.(css|js)$/)){
      e.respondWith((async () => {
        const cache = await caches.open(CACHE);
        const cached = await cache.match(req);
        const fetchPromise = fetch(req).then(res => {
          if(res.ok) cache.put(req, res.clone());
          return res;
        }).catch(()=>null);
        return cached || (await fetchPromise) || (await caches.match('/static/branding/error-fork.png')) || Response.error();
      })());
      return;
    }
    // Images/fonts: network first
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
