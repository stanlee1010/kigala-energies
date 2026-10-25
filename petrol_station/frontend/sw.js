/* ==========================================================================
   Service worker: keeps the station system usable on a slow or missing connection.
   - Pages, styles and scripts: fetched fresh when possible (so updates arrive at once);
     a saved copy is used when the network is down or too slow.
   - Data (GET /api/...): fetched fresh when possible; the last copy is shown offline.
     The copy is deleted when the person logs out.
   - Saves are never handled here: the page keeps them in its outbox (see app.js).
   Works on HTTPS (and on localhost); browsers do not allow it on plain http://<ip>.
   ========================================================================== */
const VERSION = "v3";
const SHELL = `shell-${VERSION}`;
const API = `api-${VERSION}`;
const PRECACHE = [
  "/login.html", "/change-password.html", "/404.html", "/privacy.html",
  "/staff/index.html", "/staff/records.html", "/staff/refill.html",
  "/css/style.css", "/css/forms.css", "/css/dashboard.css",
  "/js/app.js", "/js/validation.js", "/js/pages/login.js", "/js/pages/change-password.js",
  "/js/pages/staff-shift.js", "/js/pages/staff-records.js", "/js/pages/staff-refill.js",
  "/img/logo.png", "/img/logo-mark.png", "/img/login-bg.png", "/img/favicon.png",
  "/manifest.webmanifest", "/img/icon-192.png", "/img/icon-512.png", "/img/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== API).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (event) => {
  if (event.data === "clear-data") event.waitUntil(caches.delete(API));   // on log out
});

const timeout = (ms) => new Promise((resolve, reject) => setTimeout(() => reject(new Error("timeout")), ms));

async function networkFirst(request, cacheName, ms) {
  const cache = await caches.open(cacheName);
  try {
    const response = await Promise.race([fetch(request), timeout(ms)]);
    if (response.ok && response.type === "basic") {
      // Keep the time it was saved, so the page can say how old an offline copy is.
      const headers = new Headers(response.headers);
      headers.set("X-Saved-At", new Date().toISOString());
      const body = await response.clone().blob();
      cache.put(request, new Response(body, { status: response.status, statusText: response.statusText, headers }));
    }
    return response;
  } catch (err) {
    const saved = await cache.match(request, { ignoreSearch: cacheName === SHELL });
    if (saved && cacheName === API) {
      const headers = new Headers(saved.headers);
      headers.set("X-Offline-Copy", saved.headers.get("X-Saved-At") || "");
      return new Response(await saved.blob(), { status: saved.status, headers });
    }
    if (saved) return saved;
    if (cacheName === API) {
      return new Response(JSON.stringify({ error: "No internet connection.", offline: true }),
        { status: 503, headers: { "Content-Type": "application/json" } });
    }
    return (await cache.match("/404.html")) || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname === "/healthz") return;
  if (url.pathname.startsWith("/api/")) {
    // Files, training and the clock are never kept: an old saved time would make the clock wrong.
    if (/^\/api\/(attachments|reports|demo|time)/.test(url.pathname)) return;
    event.respondWith(networkFirst(request, API, 8000));
  } else {
    event.respondWith(networkFirst(request, SHELL, 5000));
  }
});
