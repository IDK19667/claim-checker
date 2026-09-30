/* Evident service worker.
   - App shell + static assets: network first, cache fallback, so every
     deploy is picked up immediately when online, and the app still opens
     offline.
   - Fonts (self-hosted): cache first; the files never change.
   - API: never cached. */

const CACHE = "evident-v36";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/privacy",
  "/manifest.webmanifest",
  "/static/fonts/woff2/librefranklin-normal-400-900.woff2",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(req, fallbackKey) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(fallbackKey || req, res.clone());
    return res;
  } catch {
    return (await cache.match(fallbackKey || req)) || Response.error();
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/og/")) return;

  if (req.mode === "navigate") {
    // Any deep link falls back to the cached shell; the page reads the URL.
    event.respondWith(networkFirst(req, url.pathname === "/privacy" ? "/privacy" : "/"));
    return;
  }
  if (url.origin !== location.origin) return;
  // Fonts are immutable files: serve from cache once seen.
  if (url.pathname.startsWith("/static/fonts/")) {
    event.respondWith(cacheFirst(req));
    return;
  }
  event.respondWith(networkFirst(req));
});
