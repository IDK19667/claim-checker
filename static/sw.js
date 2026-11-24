/* Evident service worker.
   - The home page: network first, with a time limit. When a copy is kept
     and the network has not answered in SHELL_WAIT_MS, the copy is shown
     and the network's answer, when it comes, is kept for next time. With
     no copy (a first visit) it waits for the network, as before. An
     answer of a server waking (a 5xx) or busy (a 429) shows the copy
     when one is kept; any other answer (a 403 challenge, another 4xx, a
     redirect) goes to the reader as the network gave it. Only a good
     page is stored.
   - Other pages: network first, cache fallback, each kept under its own
     address once visited; /checks and /flight, which the server sends on
     to the home page, are sent on by the worker too when offline; a result address (/?q=) falls back to the home
     page and is never kept in its place.
   - Stamped static files (?v= and the file's hash, added by the server to
     every address it prints, here included): cache first. A changed file
     gets a new address, so a stored copy is never stale; the copy under an
     older stamp of the same path is dropped when the new one is stored.
   - Unstamped static files: network first, as before.
   - Fonts (self-hosted): cache first; the files never change.
   - API: never cached. */

const CACHE = "evident-v124";
// Only what the home page needs to paint offline. Everything else (the
// other pages, style.css, the display face they use, the icons, the 3D
// files) is kept when first used. The static files here are stamped and
// kept a year, so after a first visit installing them costs no download.
const SHELL = [
  "/",
  "/static/desk.css",
  "/static/app.js",
  "/static/desk.js",
  "/static/fonts/woff2/bricolagegrotesque-opsz12-96-200-800.woff2",
  "/static/fonts/woff2/instrumentsans-normal-400-600.woff2",
  "/static/fonts/woff2/ibmplexmono-400.woff2",
  "/static/fonts/woff2/ibmplexmono-500.woff2",
];
// The pages kept under their own address once visited, for offline reading.
const PAGES = ["/privacy", "/about", "/method", "/corrections"];
// Long enough for a sound server on a slow phone line (the home page is
// about 9 KB; it arrives well inside this on slow 4G), short enough that a
// sleeping or overloaded server never leaves a returning reader on a blank
// screen for longer than about three seconds.
const SHELL_WAIT_MS = 2500;
// Old addresses the server sends on to the home page (a 301). Offline the
// worker sends them on itself, so the page shown carries the right address;
// true: the query (a claim) goes along.
const MOVED = { "/checks": true, "/flight": false };
// A home address that names a claim is a result, not the home page.
const CLAIM_ARGS = ["q", "text", "title"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(SHELL).then(() => Promise.all(SHELL.map((u) => dropOlderStamps(c, new URL(u, location.href).href)))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function homeShell(event) {
  const cache = await caches.open(CACHE);
  const net = fetch(event.request).then((res) => {
    if (res.ok) return cache.put("/", res.clone()).then(() => res, () => res);
    return res;
  });
  const kept = await cache.match("/");
  if (!kept) return net.catch(() => Response.error());
  event.waitUntil(net.catch(() => {}));  // a late answer still refreshes the copy
  const late = new Promise((r) => setTimeout(() => r(null), SHELL_WAIT_MS));
  const res = await Promise.race([net.catch(() => null), late]);
  // only a server waking up (5xx) or busy (429) is hidden behind the good copy;
  // any other answer (a 403 challenge, a redirect) is the reader's to see
  return res && !(res.status >= 500 || res.status === 429) ? res : kept;
}

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
  if (res.ok) cache.put(req, res.clone()).then(() => dropOlderStamps(cache, req.url));
  return res;
}

// one copy per path: the stored copies of this path under another stamp go
async function dropOlderStamps(cache, href) {
  const now = new URL(href);
  if (!now.searchParams.has("v")) return;
  for (const key of await cache.keys()) {
    const k = new URL(key.url);
    if (k.pathname === now.pathname && k.searchParams.has("v") && k.search !== now.search) await cache.delete(key);
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/og/")) return;
  // The owner's counts page carries a token in its address: straight to the
  // network, never stored and never served from a cache.
  if (url.pathname === "/stats" || url.pathname.startsWith("/stats/")) return;

  if (req.mode === "navigate") {
    if (url.pathname === "/" && !CLAIM_ARGS.some((k) => url.searchParams.has(k))) {
      event.respondWith(homeShell(event));
    } else if (PAGES.includes(url.pathname)) {
      event.respondWith(networkFirst(req, url.pathname));
    } else if (Object.prototype.hasOwnProperty.call(MOVED, url.pathname)) {
      const to = new URL("/" + (MOVED[url.pathname] ? url.search : ""), location.origin).href;
      event.respondWith(fetch(req).catch(() => Response.redirect(to, 301)));
    } else {
      // Any other address (a result, a deep link) falls back to the cached
      // home page, which reads the URL; it is never kept in its place.
      event.respondWith(fetch(req).catch(() => caches.open(CACHE).then((c) => c.match("/")).then((r) => r || Response.error())));
    }
    return;
  }
  if (url.origin !== location.origin) return;
  // The band's clips are streamed, so the browser asks for ranges and the
  // reply is a 206, which a Cache cannot hold. They are not shell either: a
  // check needs a connection anyway, and the stills already cover offline.
  if (/\.(mp4|webm)$/.test(url.pathname)) return;
  // Fonts are immutable files, and a stamped address names one version of
  // a file: serve from cache once seen.
  if (url.pathname.startsWith("/static/fonts/") || (url.pathname.startsWith("/static/") && url.searchParams.has("v"))) {
    event.respondWith(cacheFirst(req));
    return;
  }
  event.respondWith(networkFirst(req));
});
