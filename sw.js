// Service worker: la app abre sin red (el cascarón se cachea). El MQTT nunca pasa por acá.
const CACHE = 'casa-v3';
const SHELL = ['./', './index.html', './manifest.json', './jarvis/jarvis.js', './jarvis/oido.js', './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Stale-while-revalidate para el cascarón y las librerías de CDN
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const cdn = ['cdnjs.cloudflare.com', 'unpkg.com'].includes(url.hostname);
  if (url.origin !== location.origin && !cdn) return;
  if (/\/(modelo|vendor)\//.test(url.pathname)) {          // el modelo de voz es grande: se baja una vez y queda guardado
    e.respondWith(caches.open(CACHE).then(async c => {
      const hit = await c.match(req); if (hit) return hit;
      const r = await fetch(req); if (r && r.ok) c.put(req, r.clone()); return r;
    }));
    return;
  }
  e.respondWith(caches.open(CACHE).then(async c => {
    const hit = await c.match(req);
    const net = fetch(req).then(r => { if (r && r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
    return hit || net;
  }));
});
