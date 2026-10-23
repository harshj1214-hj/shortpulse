/* ShortPulse service worker: makes the app installable (and packageable as an
   Android APK) and shows an offline page when there's no connection.
   It NEVER caches short links, redirects, pages or API calls: every click must
   reach the server so it's counted and so a disabled link stops immediately.
   Only static files (CSS, JS, icons) are cached, network-first. */
const VERSION = "shortpulse-v1";
const SHELL = ["/static/offline.html", "/static/css/app.css", "/static/js/app.js",
               "/static/favicon.svg", "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.mode === "navigate") {
    // Pages and short links always come from the network; offline page only if that fails.
    event.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req)));
  }
  // everything else (API calls, QR images, exports) goes straight to the network
});
