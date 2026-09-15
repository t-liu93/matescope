/* global self, caches, fetch, URL */
const CACHE = "matescope-offline-v2";
const PRECACHE = ["/offline.html", "/icons/matescope-192.png", "/icons/matescope-512.png", "/icons/matescope-maskable-512.png"];
const ICONS = new Set(PRECACHE.slice(1));
self.addEventListener("install", (event) => { event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE))); });
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("matescope-offline-") && key !== CACHE).map((key) => caches.delete(key)))));
});
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname === "/api" || url.pathname.startsWith("/api/")) return;
  if (request.mode === "navigate") { event.respondWith(fetch(request).catch(() => caches.match("/offline.html"))); return; }
  if (url.search === "" && ICONS.has(url.pathname)) event.respondWith(caches.match(url.pathname).then((cached) => cached || fetch(request)));
});
