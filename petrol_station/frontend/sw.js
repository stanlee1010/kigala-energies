/* ==========================================================================
   Service worker: opens pages at once and keeps the station system usable on a slow or
   missing connection.
   - Pages, styles, scripts and images: shown from the copy saved on this device, so opening a
     page never waits for the network. Every reply from the server says which version of the
     site it runs (X-App-Version); when that changes, all saved copies are fetched again in the
     background and the next page opened uses them.
   - Data (GET /api/...): fetched fresh when possible; the last copy is shown offline.
     The copy is deleted when the person logs out.
   - Saves are never handled here: the page keeps them in its outbox (see app.js).
   Works on HTTPS (and on localhost); browsers do not allow it on plain http://<ip>.
   ========================================================================== */
const VERSION = "v4";
const API = `api-${VERSION}`;
const META = `meta-${VERSION}`;   // which saved copy of the site is in use, and its version
// Every page of the system, so each opens at once even the first time (tests/test_site_basics.py
// checks that new pages are added here).
const PRECACHE = [
  "/login.html", "/change-password.html", "/404.html", "/privacy.html", "/index.html", "/dashboard.html",
  "/staff/index.html", "/staff/records.html", "/staff/refill.html",
  "/manager/alerts.html", "/manager/audit.html", "/manager/capital.html", "/manager/expenses.html",
  "/manager/prices.html", "/manager/pumps.html", "/manager/refills.html", "/manager/reports.html",
  "/manager/settings.html", "/manager/shifts.html", "/manager/staff.html", "/manager/stock.html",
  "/director/index.html", "/director/managers.html", "/director/stations.html",
  "/css/style.css", "/css/forms.css", "/css/dashboard.css",
  "/js/app.js", "/js/validation.js", "/js/dashboard.js",
  "/js/pages/login.js", "/js/pages/change-password.js", "/js/pages/index.js",
  "/js/pages/staff-shift.js", "/js/pages/staff-records.js", "/js/pages/staff-refill.js",
  "/js/pages/manager-alerts.js", "/js/pages/manager-audit.js", "/js/pages/manager-capital.js",
  "/js/pages/manager-expenses.js", "/js/pages/manager-prices.js", "/js/pages/manager-pumps.js",
  "/js/pages/manager-refills.js", "/js/pages/manager-reports.js", "/js/pages/manager-settings.js",
  "/js/pages/manager-shifts.js", "/js/pages/manager-staff.js", "/js/pages/manager-stock.js",
  "/js/pages/director-dashboard.js", "/js/pages/director-managers.js", "/js/pages/director-stations.js",
  "/speculation-rules.json",
  "/img/logo.png", "/img/logo-mark.png", "/img/login-bg.png", "/img/favicon.png",
  "/manifest.webmanifest", "/img/icon-192.png", "/img/icon-512.png", "/img/apple-touch-icon.png",
];

/* ------------------------------------------------- which copy is in use */
let meta = null;   // { shell: cache name, version: site version }
async function getMeta() {
  if (meta) return meta;
  const saved = await (await caches.open(META)).match("/__meta");
  meta = saved ? await saved.json() : { shell: null, version: null };
  return meta;
}
async function setMeta(value) {
  meta = value;
  await (await caches.open(META)).put("/__meta", new Response(JSON.stringify(value)));
}

const pathOf = (url) => { const u = new URL(url, self.location.origin); return u.pathname; };

/** Save fresh copies of these files into a new cache (a file that cannot be fetched keeps its old copy). */
async function fillShell(urls, old) {
  const name = `shell-${Date.now()}`;
  const cache = await caches.open(name);
  let version = null;
  await Promise.all(urls.map(async (url) => {
    try {
      const response = await fetch(url, { cache: "no-store", credentials: "same-origin" });
      if (response.ok && !response.redirected) {
        version = version || response.headers.get("X-App-Version");
        await cache.put(url, response);
        return;
      }
    } catch (e) { /* offline: keep the old copy below */ }
    const previous = old && await old.match(url);
    if (previous) await cache.put(url, previous);
  }));
  return { name, version };
}

let updating = null;
/** The server runs a newer version of the site: fetch every saved file again, then switch over. */
function checkVersion(version) {
  if (!version || updating) return;
  updating = (async () => {
    const current = await getMeta();
    if (current.version === version) return;
    const old = current.shell ? await caches.open(current.shell) : null;
    const saved = old ? (await old.keys()).map((r) => pathOf(r.url)) : [];
    const fresh = await fillShell([...new Set([...PRECACHE, ...saved])], old);
    await setMeta({ shell: fresh.name, version: fresh.version || version });
    if (current.shell) await caches.delete(current.shell);
  })().catch(() => null).finally(() => { updating = null; });
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const fresh = await fillShell(PRECACHE, null);
    await setMeta({ shell: fresh.name, version: fresh.version });
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const { shell } = await getMeta();
    const keep = new Set([shell, API, META]);
    await Promise.all((await caches.keys()).filter((k) => !keep.has(k)).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data === "clear-data") event.waitUntil(caches.delete(API));   // on log out
});

/* --------------------------------------------- pages, styles, scripts, images */
async function savedFirst(request) {
  const { shell } = await getMeta();
  const cache = shell ? await caches.open(shell) : null;
  const saved = cache && await cache.match(request, { ignoreSearch: true });
  if (saved) return saved;
  try {
    const response = await fetch(request);
    checkVersion(response.headers.get("X-App-Version"));
    if (cache && response.ok && response.type === "basic" && !response.redirected) {
      await cache.put(pathOf(request.url), response.clone());
    }
    return response;
  } catch (err) {
    return (cache && await cache.match("/404.html")) || Response.error();
  }
}

/* ----------------------------------------------------------------- data */
const timeout = (ms) => new Promise((resolve, reject) => setTimeout(() => reject(new Error("timeout")), ms));

async function networkFirst(request, ms) {
  const cache = await caches.open(API);
  try {
    const response = await Promise.race([fetch(request), timeout(ms)]);
    checkVersion(response.headers.get("X-App-Version"));
    if (response.ok && response.type === "basic") {
      // Keep the time it was saved, so the page can say how old an offline copy is.
      const headers = new Headers(response.headers);
      headers.set("X-Saved-At", new Date().toISOString());
      const body = await response.clone().blob();
      cache.put(request, new Response(body, { status: response.status, statusText: response.statusText, headers }));
    }
    return response;
  } catch (err) {
    const saved = await cache.match(request);
    if (saved) {
      const headers = new Headers(saved.headers);
      headers.set("X-Offline-Copy", saved.headers.get("X-Saved-At") || "");
      return new Response(await saved.blob(), { status: saved.status, headers });
    }
    return new Response(JSON.stringify({ error: "No internet connection.", offline: true }),
      { status: 503, headers: { "Content-Type": "application/json" } });
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname === "/healthz") return;
  if (url.pathname.startsWith("/api/")) {
    // Files, training and the clock are never kept: an old saved time would make the clock wrong.
    if (/^\/api\/(attachments|reports|demo|time)/.test(url.pathname)) return;
    event.respondWith(networkFirst(request, 8000));
  } else {
    event.respondWith(savedFirst(request));
  }
});
