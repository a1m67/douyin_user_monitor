const BUILD_ID = "{{BUILD_ID}}";
const CACHE = `short-drama-shell-${BUILD_ID}`;
const SHELL = [
  "/following", "/manifest.webmanifest", "/pwa-icon.svg",
  `/static/app.css?v=${BUILD_ID}`, `/static/api.js?v=${BUILD_ID}`,
  `/static/core.js?v=${BUILD_ID}`, `/static/shows.js?v=${BUILD_ID}`,
  `/static/library.js?v=${BUILD_ID}`, `/static/system.js?v=${BUILD_ID}`,
  `/static/app.js?v=${BUILD_ID}`,
];
self.addEventListener("install", event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener("activate", event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))));
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin || event.request.method !== "GET") return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/health" || url.pathname === "/version") {
    event.respondWith(fetch(event.request));
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).then(response => {
      const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); return response;
    }).catch(() => caches.match(event.request).then(value => value || caches.match("/following"))));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request).then(response => {
    const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); return response;
  })));
});
