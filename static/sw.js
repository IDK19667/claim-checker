/* Evident service worker.
   - App shell + static assets: network first, cache fallback, so every
     deploy is picked up immediately when online, and the app still opens
     offline.
   - Fonts (self-hosted): cache first; the files never change.
   - API: never cached. */

const CACHE = "evident-v43";
const SHELL = [
  "/",
  "/checks",
  "/static/style.css",
  "/static/app.js",
  /* The home page is the fly-through now, so its stylesheet and script are
     shell files too. The frames themselves are deliberately never cached
     (see the /static/flight/ bail-out below): offline, the sequence simply
     does not load and the page falls back to the stills, with the checker
     underneath working as it always has. */
  "/static/flight.css",
  "/static/flight.js",
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
    const shell = SHELL.includes(url.pathname) ? url.pathname : "/";
    event.respondWith(networkFirst(req, shell));
    return;
  }
  if (url.origin !== location.origin) return;
  // The fly-through's frame sequence is ~23MB across three tiers. The HTTP
  // cache already handles it, and copying it into the service worker's cache
  // would spend a reader's storage quota on footage rather than on the shell
  // that has to work offline. Left to the network.
  if (url.pathname.startsWith("/static/flight/")) return;
  // Fonts are immutable files: serve from cache once seen.
  if (url.pathname.startsWith("/static/fonts/")) {
    event.respondWith(cacheFirst(req));
    return;
  }
  event.respondWith(networkFirst(req));
});
