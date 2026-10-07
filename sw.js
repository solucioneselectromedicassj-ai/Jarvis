// Service worker: deja la app instalable y abrible aunque el servidor tarde. NO toca el WebSocket.
const CACHE = "casa-v1";
const BASE = ["/", "/manifest.webmanifest", "/icono-192.png", "/icono-512.png",
  "/vendor/react.production.min.js", "/vendor/react-dom.production.min.js", "/vendor/htm.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(BASE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  // red primero (así se actualiza sola); si no hay red, lo guardado
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok) { const copia = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copia)); }
    return r;
  }).catch(() => caches.match(e.request)));
});
