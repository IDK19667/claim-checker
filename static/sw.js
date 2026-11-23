/* Evident service worker.
   - App shell + static assets: network first, cache fallback, so every
     deploy is picked up immediately when online, and the app still opens
     offline.
   - Fonts (self-hosted): cache first; the files never change.
   - API: never cached. */

const CACHE = "evident-v119";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/desk.css",
  "/static/desk.js",
  "/privacy",
  "/about",
  "/method",
  "/corrections",
  "/manifest.webmanifest",
  "/static/fonts/woff2/bricolagegrotesque-opsz96-300-500.woff2",
  "/static/fonts/woff2/instrumentsans-normal-400-600.woff2",
  "/static/fonts/woff2/ibmplexmono-500.woff2",
  "/static/fonts/woff2/bricolagegrotesque-opsz12-96-200-800.woff2",
  "/static/fonts/woff2/ibmplexmono-400.woff2",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-512-maskable.png",
  "/static/icons/apple-touch-icon.png",
];
// three.js and the wood are not shell: the desk fetches them only when it can
// draw (never on Save-Data or 2G), and the fetch handler below keeps a copy.

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
  // The owner's counts page carries a token in its address: straight to the
  // network, never stored and never served from a cache.
  if (url.pathname === "/stats" || url.pathname.startsWith("/stats/")) return;

  if (req.mode === "navigate") {
    // Any deep link falls back to the cached shell; the page reads the URL.
    const shell = SHELL.includes(url.pathname) ? url.pathname : "/";
    event.respondWith(networkFirst(req, shell));
    return;
  }
  if (url.origin !== location.origin) return;
  // The band's clips are streamed, so the browser asks for ranges and the
  // reply is a 206, which a Cache cannot hold. They are not shell either: a
  // check needs a connection anyway, and the stills already cover offline.
  if (/\.(mp4|webm)$/.test(url.pathname)) return;
  // Fonts are immutable files: serve from cache once seen.
  if (url.pathname.startsWith("/static/fonts/")) {
    event.respondWith(cacheFirst(req));
    return;
  }
  event.respondWith(networkFirst(req));
});
