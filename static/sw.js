// BlaqVibes SW — 5 Whys: Why PWA? Offline feed + installable. Why cache-first for static? Fast, offline. Why network-first for HTML? Fresh vibes. Why fallback to /oops/? Safe fork page, not scary.
// No secrets in JS — no S3 keys, no scan_report.
const CACHE = 'blaqvibes-v1';
const STATIC_ASSETS = [
  '/static/gallery/css/blaqvibes.css',
  '/static/gallery/css/error.css',
  '/static/gallery/css/forms.css',
  '/static/gallery/js/blaqvibes.js',
  '/static/gallery/js/detail.js',
  '/static/gallery/js/profile.js',
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
  // Static -> cache first
  if(url.pathname.startsWith('/static/')){
    e.respondWith(caches.match(req).then(r=>r || fetch(req).then(res=>{
      caches.open(CACHE).then(c=>c.put(req, res.clone()));
      return res;
    }).catch(()=>caches.match('/static/branding/error-fork.png'))));
    return;
  }
  // HTML -> network first, fallback to cache, then safe page
  if(req.headers.get('accept')?.includes('text/html')){
    e.respondWith(fetch(req).then(res=>{
      // Don't cache 404/403 as success — but cache 200
      if(res.ok) { const clone=res.clone(); caches.open(CACHE).then(c=>c.put(req, clone)); }
      return res;
    }).catch(()=>caches.match(req).then(r=>r || caches.match('/oops/').then(r2=>r2 || Response.error()))));
    return;
  }
});
