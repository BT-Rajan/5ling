/* Ledgerline service worker: app shell only.
 *
 * Security rules, on purpose:
 *  - Never touches /api/, cross-origin requests, or anything that is not a GET.
 *  - Caches only the build's own static files listed in PRECACHE (filled in at build time).
 *  - Page loads always go to the network first; the cached shell is used only when offline.
 *  - No client or document data is ever stored by this worker.
 */
const BUILD = "__BUILD_ID__";
const CACHE = "ledgerline-shell-" + BUILD;
const PRECACHE = __PRECACHE__;
const SHELL = "/index.html";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k.startsWith("ledgerline-shell-") && k !== CACHE).map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(SHELL)));
    return;
  }
  if (PRECACHE.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
  }
});
