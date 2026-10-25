/* ==========================================================================
   Kigala Energies — shared frontend core
   API client, formatting, page shell (sidebar/topbar), server-synced clock,
   toasts, modals, tables and the first-Wednesday price review prompt.
   Business date/time always comes from the server; the browser clock is
   only used to tick the display between syncs.
   ========================================================================== */
(function () {
  "use strict";

  const App = (window.App = {});
  App.user = null;
  App.station = null;
  App.settings = { currency: "TZS", timezone: "Africa/Dar_es_Salaam" };

  /* ------------------------------------------------------------ DOM utils */

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  App.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
  App.$ = (sel, root = document) => root.querySelector(sel);
  App.$$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  App.h = (html) => {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  };
  App.on = (root, event, selector, handler) => {
    root.addEventListener(event, (e) => {
      const target = e.target.closest(selector);
      if (target && root.contains(target)) handler(e, target);
    });
  };

  /* ------------------------------------------------------------------ API */

  App.api = async function (method, url, data, opts = {}) {
    // Saves that can wait for the internet (see "offline" below): kept on this device and sent later.
    const canQueue = method === "PUT" && !opts.noQueue && QUEUEABLE.some((r) => r.test(url));
    // Each such save gets a unique key that it keeps if it has to be sent again: the server
    // applies a key only once, so a save whose reply was lost is never applied twice.
    const key = opts.key || (canQueue ? newKey() : null);
    const savedAt = canQueue ? App.serverUtcNow().toISOString() : null;   // when Save was pressed (server time)
    if (canQueue && (!navigator.onLine || readBox().length)) {
      queue(method, url, data, key, savedAt);      // keeps the order when older saves are still waiting
      App.flushOutbox();
      return { queued: true };
    }
    // X-Requested-With proves the request comes from this site's own pages (anti-CSRF, checked by the server).
    const init = { method, headers: { Accept: "application/json", "X-Requested-With": "fetch" }, credentials: "same-origin" };
    if (key) init.headers["Idempotency-Key"] = key;
    if (opts.savedAt) init.headers["X-Saved-At"] = opts.savedAt;   // sent later: when it was typed
    if (data instanceof FormData) init.body = data;
    else if (data !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(data);
    }
    let res;
    try {
      // A very slow connection is treated like no connection for saves that can wait.
      const ctrl = canQueue ? new AbortController() : null;
      if (ctrl) { init.signal = ctrl.signal; setTimeout(() => ctrl.abort(), 20000); }
      res = await fetch(url, init);
    } catch (e) {
      if (canQueue) { queue(method, url, data, key, savedAt); return { queued: true }; }
      const err = new Error("No internet connection — this was not saved. Please try again when the connection is back.");
      err.offline = true;
      throw err;
    }
    // The service worker answered with a saved copy because there is no connection.
    const copyAt = res.headers.get("X-Offline-Copy");
    if (copyAt !== null) { App.offlineCopyAt = copyAt; renderOfflineBar(); }
    let payload = null;
    try { payload = await res.json(); } catch (e) { /* non-JSON */ }
    if (res.status === 401 && !opts.allow401) {
      location.href = "/login.html?next=" + encodeURIComponent(location.pathname + location.search);
      throw new Error("Please log in");
    }
    if (!res.ok) {
      const err = new Error((payload && payload.error) || `Request failed (${res.status})`);
      err.title = payload && payload.title;
      err.details = payload && payload.details;
      err.status = res.status;
      err.retryAfter = payload && payload.retry_after;
      throw err;
    }
    return payload;
  };
  /* ----------------------------------------------- offline: save now, send later */
  // Meter readings, online money and expense sheets saved without internet stay on this device,
  // in order, and are sent automatically when the connection is back. Every save carries its own
  // Idempotency-Key, which the server applies only once: sending it again (reply lost, two tabs)
  // can never count money twice. A save the server refuses (e.g. the shift was submitted
  // meanwhile) is kept in a "could not be sent" list until the person has seen it.
  const QUEUEABLE = [/^\/api\/shifts\/\d+\/readings\/\d+$/, /^\/api\/shifts\/\d+\/online$/,
                     /^\/api\/shifts\/\d+\/expenses$/, /^\/api\/staff\/expenses$/];
  const boxKey = () => `kigala.outbox.${App.user ? App.user.id : "anon"}`;
  const failedKey = () => `kigala.outbox-failed.${App.user ? App.user.id : "anon"}`;
  function readList(name) {
    try { return JSON.parse(localStorage.getItem(name) || "[]"); } catch (e) { return []; }
  }
  const readBox = () => readList(boxKey());
  const readFailed = () => readList(failedKey());
  function writeList(name, items) {
    try { localStorage.setItem(name, JSON.stringify(items)); } finally { renderOfflineBar(); }
  }
  const writeBox = (items) => writeList(boxKey(), items);
  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));   // plain http (station Wi-Fi): no randomUUID
    return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  }
  function queue(method, url, data, key, savedAt) {
    const items = readBox().filter((i) => !(i.method === method && i.url === url));   // newest replaces older
    items.push({ method, url, data, key: key || newKey(), savedAt: savedAt || App.serverUtcNow().toISOString(),
                 at: new Date().toISOString() });
    try { writeBox(items); } catch (e) {
      throw new Error("No internet, and this phone could not keep the change (storage is full or blocked). Nothing was saved.");
    }
  }
  /** Saves still waiting to be sent whose address starts with `prefix`. */
  App.pending = (prefix) => readBox().filter((i) => i.url.startsWith(prefix));

  let flushing = false;
  App.flushOutbox = async function () {
    if (flushing || !App.user || !navigator.onLine) return;
    // Several open tabs share one outbox: only one sends at a time.
    if (navigator.locks) return navigator.locks.request("kigala-outbox", { ifAvailable: true }, (lock) => lock && sendOutbox());
    return sendOutbox();
  };

  async function sendOutbox() {
    flushing = true;
    renderOfflineBar();
    let sent = 0;
    try {
      for (let item = readBox()[0]; item; item = readBox()[0]) {
        try {
          await App.api(item.method, item.url, item.data, { noQueue: true, allow401: true, key: item.key, savedAt: item.savedAt });
          sent += 1;
        } catch (err) {
          // No connection, server busy or logged out: keep it and try again later.
          if (err.offline || !err.status || err.status >= 500 || err.status === 429 || err.status === 401) break;
          // Refused (e.g. the shift was submitted meanwhile): keep it where the person will see it.
          writeList(failedKey(), readFailed().concat([{ ...item, error: err.message, failedAt: new Date().toISOString() }]));
          App.toast(`A change saved without internet could not be sent: ${err.message}`, "error", 12000);
        }
        writeBox(readBox().filter((i) => !(i.url === item.url && i.at === item.at)));
      }
    } finally {
      flushing = false;
      renderOfflineBar();
    }
    if (sent) {
      App.toast(`${sent} change${sent > 1 ? "s" : ""} saved without internet ${sent > 1 ? "have" : "has"} now been sent.`, "ok");
      document.dispatchEvent(new CustomEvent("outbox:sent"));
    }
  };

  const WHAT = [[/\/readings\/\d+$/, "Meter reading"], [/\/online$/, "Online money"], [/expenses$/, "Expense sheet"]];
  const describe = (item) => (WHAT.find(([r]) => r.test(item.url)) || [null, "Change"])[1];

  function showFailed() {
    const rows = readFailed().map((f) => `<li><strong>${App.esc(describe(f))}</strong>
      <span class="small muted">saved ${App.datetime(f.at)}</span><br>
      ${App.esc(f.error)}${f.data && f.data.lines ? `<br><span class="small">${f.data.lines.map((l) =>
        `${App.esc(l.name)}: ${App.money(l.amount)}`).join(" · ")}</span>` : ""}
      ${f.data && (f.data.opening_meter !== undefined || f.data.closing_meter !== undefined) ? `<br><span class="small">Opening ${App.esc(f.data.opening_meter ?? "—")}, closing ${App.esc(f.data.closing_meter ?? "—")}</span>` : ""}
      ${f.data && f.data.online_expression ? `<br><span class="small">${App.esc(f.data.online_expression)}</span>` : ""}</li>`).join("");
    App.modal({ title: "Changes that could not be sent", body: `<p>These were saved on this device without internet,
      but the system refused them when they were sent. Tell the manager so the figures can be entered.</p><ul class="stack">${rows}</ul>`,
      actions: [{ label: "Keep for now" }, { label: "I have told the manager — remove", cls: "btn--primary",
        onClick: () => writeList(failedKey(), []) }] });
  }

  function renderOfflineBar() {
    if (!document.body) return;
    let bar = document.getElementById("offline-bar");
    const waiting = App.user ? readBox().length : 0;
    const failed = App.user ? readFailed().length : 0;
    if (navigator.onLine) App.offlineCopyAt = null;
    if (navigator.onLine && !waiting && !failed) { if (bar) bar.remove(); return; }
    if (!bar) {
      bar = App.h('<div id="offline-bar" class="offline-bar" role="status" aria-live="polite"></div>');
      document.body.appendChild(bar);
      App.on(bar, "click", "[data-send-now]", () => App.flushOutbox());
      App.on(bar, "click", "[data-show-failed]", showFailed);
    }
    const saved = waiting ? `${waiting} change${waiting > 1 ? "s" : ""} saved on this device` : "";
    const oldest = waiting ? Math.min(...readBox().map((i) => Date.parse(i.at))) : Date.now();
    const tooOld = Date.now() - oldest > 12 * 3600 * 1000
      ? " ⚠️ <strong>Waiting for more than 12 hours — connect to the internet soon.</strong> Phones can delete data that waits too long, and after 3 days it can no longer be sent." : "";
    const copy = App.offlineCopyAt === null || App.offlineCopyAt === undefined ? ""
      : App.offlineCopyAt && !isNaN(new Date(App.offlineCopyAt))
        ? ` Showing information saved at ${App.datetime(App.offlineCopyAt)} — it may be out of date.`
        : " Showing information saved earlier — it may be out of date.";
    const problems = failed ? ` <button type="button" class="btn btn--sm btn--danger" data-show-failed>⚠️ ${failed} could not be sent</button>` : "";
    bar.innerHTML = (!navigator.onLine
      ? `📴 <strong>No internet.</strong> ${saved ? `${saved} — sent automatically when the internet is back.` : "Readings, online money and expenses you save are kept on this device."}${tooOld}${copy}`
      : waiting ? `📤 ${flushing ? "Sending" : "Waiting to send"}: ${saved}.${tooOld} ${flushing ? "" : '<button type="button" class="btn btn--sm" data-send-now>Send now</button>'}`
        : "") + problems;
  }
  /** The message pages show after a save that is waiting for the internet. */
  App.savedOffline = () => App.toast("Saved on this device. It will be sent automatically when the internet is back.", "warn", 6000);

  window.addEventListener("online", () => { renderOfflineBar(); App.flushOutbox(); });
  window.addEventListener("offline", renderOfflineBar);

  App.get = (url) => App.api("GET", url);
  App.post = (url, data = {}) => App.api("POST", url, data);
  App.put = (url, data = {}) => App.api("PUT", url, data);
  App.del = (url) => App.api("DELETE", url);

  App.qs = (params) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") q.set(k, v); });
    const s = q.toString();
    return s ? "?" + s : "";
  };

  /* ----------------------------------------------------------- formatting */

  const nf = (min, max) => new Intl.NumberFormat("en-US", { minimumFractionDigits: min, maximumFractionDigits: max });
  const NF0 = nf(0, 0), NF2 = nf(0, 2), NF3 = nf(0, 3);

  App.num = (v, digits = 2) => (v === null || v === undefined || v === "" ? "—" : (digits === 0 ? NF0 : digits === 3 ? NF3 : NF2).format(Number(v)));
  App.money = (v, withCurrency = true) => {
    if (v === null || v === undefined || v === "") return "—";
    // Whole shillings for large amounts keeps figures readable; small amounts (prices) keep cents.
    const n = Number(v);
    const s = (Math.abs(n) >= 1000 ? NF0 : NF2).format(n);
    return withCurrency ? `${App.settings.currency} ${s}` : s;
  };
  App.litres = (v) => (v === null || v === undefined || v === "" ? "—" : `${NF2.format(Number(v))} L`);
  App.date = (v) => {
    if (!v) return "—";
    const [y, m, d] = String(v).slice(0, 10).split("-");
    return App.esc(`${d}/${m}/${y}`);
  };
  /** Station-local naive ISO string, e.g. 2026-09-28T06:00:00 */
  App.localDateTime = (v) => (v ? `${App.date(v)} ${String(v).slice(11, 16)}` : "—");
  /** UTC ISO string from the server -> station wall-clock time */
  App.datetime = (v) => {
    if (!v) return "—";
    const d = new Date(v.endsWith("Z") ? v : v + "Z");
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: (App.settings || {}).timezone, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(d).replace(",", "");
  };
  // Codes like OPEN or NIGHT_SHIFT -> "Night shift". Characters that could form HTML are dropped.
  App.label = (s) => String(s || "").replace(/[<>&"'`]/g, "").replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
  App.badge = (status, text) => `<span class="badge badge--${App.esc(status)}">${App.esc(text || App.label(status))}</span>`;
  App.fuelDot = (color) => `<span class="fuel-dot" style="background:${App.esc(color || "#64748b")}"></span>`;
  App.signed = (v, fmt = App.num) => (Number(v) > 0 ? "+" : "") + fmt(v);

  /* ------------------------------------------------------- toast & modal */

  App.toast = function (message, type = "info", ms = 4500) {
    let box = App.$(".toasts");
    if (!box) { box = App.h('<div class="toasts" role="status" aria-live="polite"></div>'); document.body.appendChild(box); }
    const el = App.h(`<div class="toast toast--${type}">${App.esc(message)}</div>`);
    box.appendChild(el);
    setTimeout(() => el.remove(), ms);
  };
  App.error = (err) => {
    // Business warnings with a title (e.g. TANK CAPACITY EXCEEDED) get a clear pop-up, others a toast.
    if (err && err.title) {
      App.modal({ title: `⚠️ ${App.esc(err.title)}`, body: `<p>${App.esc(err.message)}</p>`,
                  actions: [{ label: "OK", cls: "btn--primary" }] });
    } else {
      App.toast(err && err.message ? err.message : String(err), "error", 7000);
    }
  };

  /**
   * App.modal({ title, body, wide, dismissible, actions: [{ label, cls, onClick(close, modalEl) }] })
   * onClick may return false (or a Promise resolving to false) to keep the modal open.
   */
  App.modal = function ({ title, body, wide = false, dismissible = true, actions = [] }) {
    const layer = App.h(`
      <div class="modal-layer" role="dialog" aria-modal="true">
        <div class="modal ${wide ? "modal--wide" : ""}">
          <div class="modal__head"><h2>${title}</h2>${dismissible ? '<button class="modal__close" aria-label="Close">&times;</button>' : ""}</div>
          <div class="modal__body"></div>
          ${actions.length ? '<div class="modal__foot"></div>' : ""}
        </div>
      </div>`);
    const bodyEl = App.$(".modal__body", layer);
    if (typeof body === "string") bodyEl.innerHTML = body; else if (body) bodyEl.appendChild(body);
    const close = () => { layer.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape" && dismissible) close(); };
    document.addEventListener("keydown", onKey);
    if (dismissible) {
      App.$(".modal__close", layer).addEventListener("click", close);
      layer.addEventListener("mousedown", (e) => { if (e.target === layer) close(); });
    }
    const foot = App.$(".modal__foot", layer);
    actions.forEach((a) => {
      const btn = App.h(`<button type="button" class="btn ${a.cls || ""}">${a.label}</button>`);
      btn.addEventListener("click", async () => {
        if (!a.onClick) return close();
        btn.disabled = true;
        try {
          const keep = await a.onClick(close, layer);
          if (keep !== false) close();
        } catch (err) { App.error(err); } finally { btn.disabled = false; }
      });
      foot.appendChild(btn);
    });
    document.body.appendChild(layer);
    const first = App.$("input, select, textarea", bodyEl);
    if (first) setTimeout(() => first.focus(), 30);
    return { close, el: layer };
  };

  /** Ask for confirmation, optionally with a required text input (e.g. reason). Resolves value/true or false. */
  App.ask = function ({ title, message = "", confirmLabel = "Confirm", danger = false, input = null }) {
    return new Promise((resolve) => {
      const body = `<p>${message}</p>${input ? `
        <div class="field"><label for="ask-input">${App.esc(input.label)}</label>
        <textarea id="ask-input" class="input" ${input.required ? "required" : ""}>${App.esc(input.value || "")}</textarea></div>` : ""}`;
      let done = false;
      const m = App.modal({
        title, body,
        actions: [
          { label: "Cancel", onClick: () => { done = true; resolve(false); } },
          {
            label: confirmLabel, cls: danger ? "btn--danger" : "btn--primary",
            onClick: (close, el) => {
              const val = input ? App.$("#ask-input", el).value.trim() : true;
              if (input && input.required && !val) { App.toast(`${input.label} is required`, "error"); return false; }
              done = true; resolve(val);
            },
          },
        ],
      });
      const obs = new MutationObserver(() => { if (!document.body.contains(m.el)) { obs.disconnect(); if (!done) resolve(false); } });
      obs.observe(document.body, { childList: true });
    });
  };

  /** One tank's movement in a shift: Opening + Refills − (each pump = total sold) = expected balance. */
  App.tankCard = (t) => `
    <div class="reading-card" style="border-left-color:${App.esc(t.color)}">
      <strong>${App.fuelDot(t.color)}${App.esc(t.tank)}</strong>
      <span class="small muted"> · capacity ${App.litres(t.capacity)}</span>
      <table class="pl-table">
        <tr><td>Opening stock</td><td>${App.litres(t.opening)}</td></tr>
        <tr><td>+ Refilled into the tank</td><td>${App.litres(t.refilled)}</td></tr>
        ${t.pumps.map((p) => `<tr class="small"><td>&nbsp;&nbsp;${App.esc(p.pump)} dispensed</td><td>${App.litres(p.sold)}</td></tr>`).join("")}
        <tr><td>− Total sold (all pumps on this tank)</td><td>${App.litres(t.sold)}</td></tr>
        ${Number(t.adjustments) ? `<tr><td>± Adjustments</td><td>${App.signed(t.adjustments, App.litres)}</td></tr>` : ""}
        <tr class="total ${t.over_capacity || t.below_zero ? "text-danger" : ""}"><td>= Expected tank balance</td><td>${App.litres(t.closing)}</td></tr>
      </table>
      ${t.over_capacity ? '<p class="small text-danger"><strong>⚠️ More than the tank can hold — check the refills and readings.</strong></p>' : ""}
      ${t.below_zero ? '<p class="small text-danger"><strong>⚠️ Below zero — record the tank’s opening balance or check for a missing refill.</strong></p>' : ""}
    </div>`;

  /**
   * MANAGER: money for a day — Morning (opening shift) first, any added shift, then Night.
   * Each shift's cash uses only its OWN online money; the last row is the total for all shifts.
   */
  App.dayMoneyHtml = (d) => `
    ${App.table({
      columns: [
        { key: "name", label: "Shift", render: (s) => `${App.esc(s.name)} <span class="small muted">${App.label(s.status)}</span>` },
        { key: "total", label: "Total (sales)", cls: "num", render: (s) => App.money(s.total) },
        { key: "online", label: "− Its online (as typed)", cls: "num", render: (s) => App.money(s.online) +
            (s.online_expression ? `<div class="small muted">${App.esc(s.online_expression.split("+").join(" + "))}</div>` : "") },
        { key: "total_cash", label: "= Total cash", cls: "num", render: (s) => `<strong>${App.money(s.total_cash)}</strong>` },
      ],
      rows: d.shifts, cards: false, empty: "No shifts for this day yet.",
      footer: [{ html: "All shifts" }, { cls: "num", html: App.money(d.total) }, { cls: "num", html: App.money(d.online_total) },
               { cls: "num", html: App.money(d.total_cash) }],
    })}
    <table class="pl-table" style="margin-top:.5rem">
      <tr><td>Total money obtained (all shifts)</td><td>${App.money(d.total)}</td></tr>
      <tr><td>Total money online per day</td><td>${App.money(d.online_total)}</td></tr>
      <tr><td>Total cash of the day</td><td>${App.money(d.total_cash)}</td></tr>
      <tr><td>− Expenses of the day</td><td>${App.money(d.expenses)}</td></tr>
      <tr class="total ${Number(d.balance) < 0 ? "text-danger" : "text-ok"}"><td>= Balance of the day</td><td>${App.money(d.balance)}</td></tr>
    </table>
    <p class="small muted">The Morning shift opens the day. Each shift's cash = its own sales − its own online money.</p>`;

  /**
   * Expense form. The category is typed in the person's own words, with the existing
   * categories suggested as they type (a new word becomes a new category).
   * options: { date: true to show a date field, max: latest allowed date, reason: true to ask why }
   */
  let expenseFormSeq = 0;
  App.expenseForm = function (e, categories, options = {}) {
    const id = `cat-list-${++expenseFormSeq}`;
    const form = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Category</label>
        <input class="input" name="category_name" list="${id}" autocomplete="off" required
               placeholder="Type or pick, e.g. Cleaning" value="${App.esc(e ? e.category : "")}">
        <datalist id="${id}">${categories.filter((c) => c.is_active).map((c) => `<option value="${App.esc(c.name)}">`).join("")}</datalist>
        <span class="hint">Pick a suggestion or write your own words.</span></div>
      <div class="field"><label>Amount</label><input class="input input--big" type="number" inputmode="decimal" min="0" step="0.01" name="amount" value="${e ? Number(e.amount) : ""}" data-positive required></div>
      ${options.date ? `<div class="field"><label>Date</label><input class="input" type="date" name="expense_date" value="${e ? e.expense_date : App.serverToday()}" max="${options.max || App.serverToday()}" required></div>` : ""}
      <div class="field"><label>Reference</label><input class="input" name="reference" value="${App.esc(e ? e.reference || "" : "")}"></div>
      <div class="field span-all"><label>What was it for? (your own words)</label><input class="input" name="description" value="${App.esc(e ? e.description : "")}" required></div>
      <div class="field span-all"><label>Receipt photo (optional)</label><input class="input" type="file" name="receipt" accept="image/*,application/pdf" capture="environment"></div>
      ${options.reason ? '<div class="field span-all"><label>Reason for change</label><input class="input" name="reason" required></div>' : ""}
    </div></form>`);
    return form;
  };

  /** "Matumizi" expense sheet: one line per expense (own words + amount), ➕ / − lines, running total.
   *  items: expenses to show; lines the person may still change are editable, the rest are fixed.
   *  save(lines) sends [{id, name, amount}] and resolves when saved (the caller then redraws). */
  /** Expense items with any sheet saved offline (not sent yet) shown in place of the person's pending lines. */
  window.withPendingLines = function (items, url) {
    const waiting = App.pending(url)[0];
    if (!waiting) return items;
    const decided = items.filter((e) => e.locked || !(e.status === "PENDING" && e.entered_by === App.user.id));
    return decided.concat(waiting.data.lines.map((l) => ({ id: l.id, category: l.name, description: l.name, was: l.was,  // (not sent yet)
      amount: l.amount, status: "PENDING", entered_by: App.user.id })));
  };
  let expenseSheetSeq = 0;
  App.expenseSheet = function (box, { items, editable, categories, save, title = "Matumizi — expenses" }) {
    const listId = `exp-words-${++expenseSheetSeq}`;
    const canEdit = (e) => editable && !e.locked && e.status === "PENDING" && e.entered_by === App.user.id;
    // Saved lines remember their values when the sheet was opened, so the server can refuse a save
    // that would overwrite a correction the manager made meanwhile.
    const was = (e) => (e && e.id ? e.was || { name: e.category, amount: e.amount } : null);
    const line = (e) => `<tr data-line data-id="${e ? e.id : ""}" ${was(e) ? `data-was-name="${App.esc(was(e).name)}" data-was-amount="${App.esc(was(e).amount)}"` : ""}>
      <td><input class="input" name="exp-name" list="${listId}" autocomplete="off" maxlength="60"
                 placeholder="e.g. Umeme" aria-label="Expense in your own words" value="${App.esc(e ? e.category : "")}"></td>
      <td><input class="input num" name="exp-amount" type="number" inputmode="decimal" min="0" step="0.01"
                 placeholder="0" aria-label="Amount" value="${e ? Number(e.amount) : ""}"></td>
      <td><button type="button" class="btn btn--sm btn--danger exp-sheet__btn" data-exp-remove title="Remove this line" aria-label="Remove this line">−</button></td>
    </tr>`;
    const fixed = items.filter((e) => !canEdit(e)).map((e) => `<tr data-fixed="${Number(e.amount)}">
      <td>${App.esc(e.category)}${e.description && e.description !== e.category ? `<div class="small muted">${App.esc(e.description)}</div>` : ""}</td>
      <td class="num">${App.money(e.amount)}</td><td>${App.badge(e.status)}</td></tr>`).join("");
    const mine = items.filter(canEdit);
    box.innerHTML = `<h3>${App.esc(title)}</h3>
      <datalist id="${listId}">${categories.filter((c) => c.is_active).map((c) => `<option value="${App.esc(c.name)}">`).join("")}</datalist>
      <div class="table-wrap"><table class="table exp-sheet">
        <thead><tr><th>Expense</th><th class="num">Amount</th><th></th></tr></thead>
        <tbody>${fixed}${editable ? (mine.length ? mine.map(line).join("") : line(null)) : ""}</tbody>
        <tfoot><tr><td><strong>TOTAL</strong></td><td class="num"><strong data-exp-total></strong></td><td></td></tr></tfoot>
      </table></div>
      ${!editable && !items.length ? '<p class="empty">No expenses.</p>' : ""}
      ${editable ? `<div class="form-actions">
        <button type="button" class="btn btn--lg" data-exp-add>➕ Add line</button>
        <button type="button" class="btn btn--primary btn--lg" data-exp-save>Save expenses</button></div>` : ""}`;
    const body = App.$("tbody", box);
    const total = () => {
      let sum = 0;
      App.$$("[data-fixed]", body).forEach((r) => { sum += Number(r.dataset.fixed) || 0; });
      App.$$('[name="exp-amount"]', body).forEach((i) => { sum += Number(i.value) || 0; });
      App.$("[data-exp-total]", box).textContent = App.money(sum);
    };
    total();
    if (!editable) return;
    body.addEventListener("input", total);
    App.on(body, "click", "[data-exp-remove]", (e, b) => { b.closest("tr").remove(); total(); });
    App.$("[data-exp-add]", box).addEventListener("click", () => {
      body.insertAdjacentHTML("beforeend", line(null));
      App.$$('[name="exp-name"]', body).pop().focus();
    });
    App.$("[data-exp-save]", box).addEventListener("click", () => App.run(async () => {
      const lines = [];
      for (const row of App.$$("[data-line]", body)) {
        const name = App.$('[name="exp-name"]', row), amount = App.$('[name="exp-amount"]', row);
        Validation.setError(name, null); Validation.setError(amount, null);
        if (!name.value.trim() && !amount.value.trim()) continue;          // empty line: ignored
        if (!name.value.trim()) { Validation.setError(name, "Write what it was for"); name.focus(); return; }
        if (!(Number(amount.value) > 0)) { Validation.setError(amount, "Enter the amount"); amount.focus(); return; }
        lines.push({ id: row.dataset.id ? Number(row.dataset.id) : null, name: name.value.trim(), amount: amount.value,
                     was: row.dataset.id ? { name: row.dataset.wasName, amount: row.dataset.wasAmount } : undefined });
      }
      const res = await save(lines);
      if (res.queued) App.savedOffline();
      else App.toast(`Expenses saved (${res.added} added, ${res.changed} changed, ${res.removed} removed).`, "ok");
    }));
  };

  /** Read an expense form, uploading the receipt first if one was chosen. */
  App.expenseData = async function (form) {
    const data = App.formData(form);
    delete data.receipt;
    const file = App.$('[name="receipt"]', form).files[0];
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      data.receipt_attachment_id = (await App.api("POST", "/api/attachments", fd)).attachment.id;
    }
    return data;
  };

  /** Collect named form fields into a plain object. */
  App.formData = function (form) {
    const data = {};
    App.$$("[name]", form).forEach((el) => {
      if (el.type === "checkbox") data[el.name] = el.checked;
      else if (el.type === "radio") { if (el.checked) data[el.name] = el.value; }
      else if (el.multiple) data[el.name] = Array.from(el.selectedOptions).map((o) => o.value);
      else data[el.name] = el.value.trim();
    });
    return data;
  };

  /** Render a table. columns: [{ key, label, cls, render(row) }] */
  App.table = function ({ columns, rows, empty = "No records found.", footer = null, cards = true }) {
    if (!rows || !rows.length) return `<div class="empty">${App.esc(empty)}</div>`;
    const head = columns.map((c) => `<th class="${c.cls || ""}">${App.esc(c.label)}</th>`).join("");
    const body = rows.map((row) => `<tr>${columns.map((c) => {
      const val = c.render ? c.render(row) : App.esc(row[c.key] ?? "—");
      return `<td class="${c.cls || ""}" data-label="${App.esc(c.label)}">${val}</td>`;
    }).join("")}</tr>`).join("");
    const foot = footer ? `<tfoot><tr>${footer.map((f) => `<td class="${f.cls || ""}">${f.html ?? ""}</td>`).join("")}</tr></tfoot>` : "";
    return `<div class="table-wrap"><table class="table ${cards ? "table--cards" : ""}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody>${foot}</table></div>`;
  };

  App.options = (items, valueKey, labelKey, selected, placeholder) =>
    (placeholder ? `<option value="">${App.esc(placeholder)}</option>` : "") +
    items.map((i) => `<option value="${App.esc(i[valueKey])}" ${String(i[valueKey]) === String(selected) ? "selected" : ""}>${App.esc(typeof labelKey === "function" ? labelKey(i) : i[labelKey])}</option>`).join("");

  App.download = function (url) {
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  /* ---------------------------------------------------------- page shell */

  const NAV = {
    MANAGER: [
      ["/dashboard.html", "📊", "Dashboard"],
      ["/manager/alerts.html", "⚠️", "Warnings", "alerts"],
      ["/manager/shifts.html", "🕒", "Shifts"],
      ["/manager/staff.html", "👥", "Staff"],
      ["/manager/pumps.html", "⛽", "Pumps"],
      ["/manager/prices.html", "🏷️", "Fuel Prices"],
      ["/manager/capital.html", "💰", "Capital"],
      ["/manager/stock.html", "🛢️", "Tanks & Stock"],
      ["/manager/refills.html", "🚚", "Refills"],
      ["/manager/expenses.html", "🧾", "Expenses"],
      ["/manager/reports.html", "📑", "Reports"],
      ["/manager/audit.html", "🔍", "Audit Log"],
      ["/manager/settings.html", "⚙️", "Settings"],
    ],
    DIRECTOR: [
      ["/director/index.html", "📊", "Sales & Profit"],
      ["/director/managers.html", "🧑‍💼", "Managers"],
      ["/director/stations.html", "📍", "Stations"],
    ],
    STAFF: [
      ["/staff/index.html", "🕒", "My Shift"],
      ["/staff/refill.html", "🚚", "Fuel Refill"],
      ["/staff/records.html?tab=expenses", "💸", "Expenses"],
      ["/staff/records.html", "🧾", "My Records"],
    ],
  };

  App.home = (user) => (!user ? "/login.html"
    : user.role === "DIRECTOR" ? "/director/index.html"
    : user.role === "MANAGER" ? "/dashboard.html" : "/staff/index.html");

  function renderShell(title) {
    const shell = App.$("#shell");
    const main = App.$(".main", shell);
    const path = location.pathname.replace(/\/$/, "/index.html");
    const items = NAV[App.user.role] || [];
    // Managers who run several locations choose the station they are working on.
    const stations = App.user.stations || [];
    const switcher = App.user.role === "MANAGER" && stations.length > 1
      ? `<label class="station-switch">📍 <select id="station-switch" class="input input--sm" aria-label="Station">
           ${App.options(stations, "id", "name", App.user.station_id)}</select></label>` : "";
    const isHere = (href) => path + location.search === href || (path === href && !location.search);
    const nav = items.map(([href, icon, label, counter]) => `
      <li><a href="${href}" class="${isHere(href) ? "active" : ""}" ${isHere(href) ? 'aria-current="page"' : ""}>
        <span class="nav__icon" aria-hidden="true">${icon}</span>${label}${counter ? `<span class="nav__count hidden" data-counter="${counter}"></span>` : ""}
      </a></li>`).join("");
    // Breadcrumbs: Home › this page (the first menu item is each role's home).
    const [homeHref, , homeLabel] = items[0] || [App.home(App.user), "", "Home"];
    const current = items.find(([href]) => isHere(href));
    const crumbs = current && current[0] === homeHref
      ? `<span aria-current="page">${App.esc(homeLabel)}</span>`
      : `<a href="${homeHref}">${App.esc(homeLabel)}</a> <span aria-hidden="true">›</span> <span aria-current="page">${App.esc(current ? current[2] : title)}</span>`;
    shell.insertBefore(App.h(`
      <aside class="sidebar" aria-label="Main navigation">
        <div class="sidebar__brand"><img class="sidebar__wordmark" src="/img/login-bg.png" alt="Kigala Energy — oil and gas" width="150" height="138"></div>
        <ul class="nav">${nav}
          <li><a href="/change-password.html"><span class="nav__icon" aria-hidden="true">🔑</span>Change password</a></li>
          ${App.user.role === "STAFF" ? '<li><a href="/training.html" target="_blank" rel="noopener"><span class="nav__icon" aria-hidden="true">📘</span>Training guide</a></li>' : ""}
          <li><a href="/privacy.html"><span class="nav__icon" aria-hidden="true">🔒</span>Privacy policy</a></li>
        </ul>
        <div class="sidebar__user"><strong>${App.esc(App.user.full_name)}</strong>
          ${App.esc(App.user.username)} · ${App.label(App.user.role)}
          <div><button class="btn btn--sm btn--ghost" id="logout-btn">Log out</button></div>
        </div>
      </aside>`), main);
    shell.insertBefore(App.h('<div class="backdrop"></div>'), main);
    main.insertBefore(App.h(`
      <header class="topbar">
        <button class="menu-toggle" aria-label="Open menu">☰</button>
        <div class="topbar__title">${App.esc(title)}</div>
        ${switcher}
        <div class="topbar__clock"><strong id="clock-time">--:--:--</strong><span id="clock-date"></span></div>
      </header>`), main.firstChild);
    document.title = `${title} · ${App.user.role === "DIRECTOR" ? "Director" : App.station.name}`;
    // Placed just above the content area, because pages re-draw their content area when they load.
    const content = App.$(".content", main);
    if (content) content.before(App.h(`<nav class="breadcrumbs breadcrumbs--shell" aria-label="Breadcrumb">${crumbs}</nav>`));
    const pick = App.$("#station-switch");
    if (pick) pick.addEventListener("change", () => App.run(async () => {
      await App.post("/api/auth/station", { station_id: Number(pick.value) });
      location.reload();   // everything on the page now belongs to the chosen station
    }));

    App.$(".menu-toggle").addEventListener("click", () => shell.classList.add("nav-open"));
    App.$(".backdrop").addEventListener("click", () => shell.classList.remove("nav-open"));
    App.$("#logout-btn").addEventListener("click", () => App.run(async () => {
      const waiting = readBox().length;
      if (waiting && !(await App.ask({ title: "Changes not sent yet", confirmLabel: "Log out anyway", danger: true,
        message: `${waiting} change(s) saved without internet are still on this device. They will be sent the next time you log in here. Log out anyway?` }))) return;
      await App.post("/api/auth/logout").catch(() => null);
      if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage("clear-data");
      location.href = "/login.html";
    }));
  }

  /* ------------------------------------------------ training portal banner */

  function renderTrainingBanner() {
    const banner = App.h(`
      <div class="training-banner" role="note">
        <strong>🎓 TRAINING PORTAL</strong>
        <span>Practice data only — nothing you do here affects the real station. The clock is paused so your practice shift never runs out.</span>
        <span class="spacer"></span>
        <a class="btn btn--sm" href="/training.html" target="_blank" rel="noopener">📘 Training guide</a>
        <button class="btn btn--sm" data-demo="reset">↺ Reset training data</button>
      </div>`);
    const topbar = App.$(".topbar");
    topbar.parentNode.insertBefore(banner, topbar.nextSibling);
    App.on(banner, "click", "[data-demo]", () => App.run(async () => {
      if (!(await App.ask({ title: "Reset training data?", confirmLabel: "Reset",
        message: "All practice changes are wiped and fresh practice data is created. Everyone on the training portal will need to log in again." }))) return;
      await App.post("/api/demo/reset");
      location.href = "/login.html";
    }));
  }

  /* ----------------------------------------------- install on the phone (keeps offline saves safe) */
  // Phones (iPhones especially) can delete a website's stored data when it is not opened for some
  // days; an app added to the home screen keeps it. Staff are invited once (dismiss = 14 days).
  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; showInstallTip(); });
  const installed = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

  function offerInstall() {
    if (!window.isSecureContext || installed() || App.demo) return;
    let until = 0;
    try { until = Number(localStorage.getItem("kigala.install-dismissed") || 0); } catch (e) { /* ignore */ }
    if (Date.now() < until) return;
    App.wantsInstallTip = true;
    showInstallTip();
  }

  function showInstallTip() {
    if (!App.wantsInstallTip || document.getElementById("install-tip")) return;
    const content = App.$(".content");
    if (!content) return;
    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const how = installPrompt ? "" : ios
      ? "Tap <strong>Share</strong> (the square with an arrow) and then <strong>Add to Home Screen</strong>."
      : "Open the browser menu (⋮) and choose <strong>Install app</strong> or <strong>Add to Home screen</strong>.";
    const tip = App.h(`<div id="install-tip" class="install-tip" role="note">
      <span>📲 <strong>Install the station app on this phone</strong> so readings and expenses saved without internet are kept safely. ${how}</span>
      <span class="install-tip__actions">${installPrompt ? '<button type="button" class="btn btn--sm btn--primary" data-install>Install</button>' : ""}
      <button type="button" class="btn btn--sm" data-install-later>Not now</button></span></div>`);
    content.before(tip);
    App.on(tip, "click", "[data-install]", async () => {
      installPrompt.prompt();
      await installPrompt.userChoice.catch(() => null);
      installPrompt = null;
      tip.remove();
    });
    App.on(tip, "click", "[data-install-later]", () => {
      try { localStorage.setItem("kigala.install-dismissed", String(Date.now() + 14 * 86400 * 1000)); } catch (e) { /* ignore */ }
      tip.remove();
    });
  }

  /* ----------------------------------------------- server-synchronised clock */

  // The last offsets measured against the server are kept on the device, so a page opened without
  // internet still shows (and stamps offline saves with) server time, not the phone's own clock.
  const savedClock = (() => { try { return JSON.parse(localStorage.getItem("kigala.clock") || "{}"); } catch (e) { return {}; } })();
  let clockOffset = savedClock.station || 0;  // server wall clock (as UTC ms) minus browser ms
  let utcOffset = savedClock.utc || 0;        // server UTC minus browser ms
  let pausedAt = null;  // training portal: the business clock does not move
  App.serverNow = () => new Date(pausedAt ?? Date.now() + clockOffset); // read with timeZone "UTC"
  App.serverToday = () => App.serverNow().toISOString().slice(0, 10);
  /** The server's UTC time now (for "typed at" on saves made offline). */
  App.serverUtcNow = () => new Date(Date.now() + utcOffset);

  async function syncClock() {
    try {
      const t = await App.get("/api/time");
      clockOffset = Date.parse(t.now + "Z") - Date.now();
      if (t.utc) utcOffset = Date.parse(t.utc + "Z") - Date.now();
      try { localStorage.setItem("kigala.clock", JSON.stringify({ station: clockOffset, utc: utcOffset })); } catch (e) { /* ignore */ }
      pausedAt = t.paused ? Date.parse(t.now + "Z") : null;
      App.currentShiftLabel = t.current_shift;
      const el = App.$("#clock-date");
      if (el) el.dataset.shift = t.current_shift;
    } catch (e) { /* keep ticking with the previous offset */ }
  }

  function tick() {
    const now = App.serverNow();
    const t = App.$("#clock-time"), d = App.$("#clock-date");
    if (!t) return;
    t.textContent = pausedAt !== null ? `⏸ ${now.toISOString().slice(11, 16)} training time` : now.toISOString().slice(11, 19);
    const day = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "2-digit", month: "short", year: "numeric" }).format(now);
    d.textContent = `${day}${d.dataset.shift ? " · " + d.dataset.shift : ""}`;
  }

  /* ----------------------------------------------------- warnings counter */

  App.refreshAlertCount = async function () {
    if (!App.user || App.user.role !== "MANAGER") return;
    try {
      const res = await App.get("/api/alerts?resolved=0");
      const el = App.$('[data-counter="alerts"]');
      if (el) { el.textContent = res.alerts.length; el.classList.toggle("hidden", !res.alerts.length); }
    } catch (e) { /* ignore */ }
  };

  /* ------------------------------------------ first-Wednesday price review */

  App.showPriceReview = function (status, onDone) {
    const rows = status.current_prices.map((p) => `
      <div class="card" data-fuel="${p.fuel_type_id}">
        <div class="card__head"><h3>${App.fuelDot(p.color)}CURRENT ${App.esc(p.fuel.toUpperCase())} PRICE</h3>
          <strong>${p.price_per_litre ? App.money(p.price_per_litre) + " / L" : "Not set"}</strong></div>
        <div class="row">
          <label class="checkbox"><input type="radio" name="d-${p.fuel_type_id}" value="KEEP" ${p.price_per_litre ? "" : "disabled"}> KEEP CURRENT PRICE</label>
          <label class="checkbox"><input type="radio" name="d-${p.fuel_type_id}" value="UPDATE" ${p.price_per_litre ? "" : "checked"}> UPDATE PRICE</label>
        </div>
        <div class="field mt hidden" data-new><label>New ${App.esc(p.fuel)} price per litre (${App.esc(App.settings.currency)})</label>
          <input class="input input--big" type="number" inputmode="decimal" min="0" step="0.01" data-price></div>
      </div>`).join("");
    const body = App.h(`<div class="stack">
      <div class="banner banner--warn"><div class="banner__icon">⛽</div><div class="banner__body">
        <h2>FUEL PRICE UPDATE REQUIRED</h2>
        <p>${status.is_first_wednesday ? "Today is the first Wednesday of the new reporting period." : "A new reporting period has started."}
        Please review and update the current fuel selling prices before continuing.</p></div></div>
      ${rows}</div>`);
    const sync = () => App.$$("[data-fuel]", body).forEach((card) => {
      const choice = App.$("input[type=radio]:checked", card);
      App.$("[data-new]", card).classList.toggle("hidden", !choice || choice.value !== "UPDATE");
    });
    body.addEventListener("change", sync);
    sync();
    App.modal({
      title: "⛽ Fuel price review", body, dismissible: false,
      actions: [{
        label: "Confirm prices", cls: "btn--primary btn--lg",
        onClick: async () => {
          const decisions = [];
          for (const card of App.$$("[data-fuel]", body)) {
            const choice = App.$("input[type=radio]:checked", card);
            if (!choice) { App.toast("Choose Keep or Update for every fuel", "error"); return false; }
            const d = { fuel_type_id: Number(card.dataset.fuel), action: choice.value };
            if (choice.value === "UPDATE") {
              d.price_per_litre = App.$("[data-price]", card).value;
              if (!(Number(d.price_per_litre) > 0)) { App.toast("Enter a valid new price", "error"); return false; }
            }
            decisions.push(d);
          }
          await App.post("/api/prices/review", { decisions });
          App.toast("Fuel prices confirmed and recorded.", "ok");
          if (onDone) onDone();
        },
      }],
    });
  };

  async function managerPrompts() {
    try {
      const status = await App.get("/api/periods/status");
      App.periodStatus = status;
      if (status.price_review_required) App.showPriceReview(status, () => location.reload());
    } catch (e) { /* non-fatal */ }
  }

  /* ---------------------------------------------------------------- init */

  /**
   * Every protected page calls App.init({ role, title }).
   * role: "MANAGER" | "STAFF" | null (any logged-in user)
   */
  App.init = async function ({ role = null, title = "" } = {}) {
    // Asked together (each trip to the server takes time); settings are only used once logged in.
    const settings = App.get("/api/settings");
    settings.catch(() => null);
    const me = await App.get("/api/auth/me");
    App.user = me.user;
    const onPasswordPage = location.pathname.endsWith("/change-password.html");
    if (App.user.must_change_password && !onPasswordPage) {
      location.href = "/change-password.html";
      return new Promise(() => {});
    }
    if (role && App.user.role !== role) {
      location.href = App.home(App.user);
      return new Promise(() => {});
    }
    const s = await settings;
    App.station = s.station;
    App.settings = s.settings;
    App.demo = !!s.demo;
    renderShell(title);
    if (App.demo) renderTrainingBanner();
    // Offline support: saved copies of pages/data (needs HTTPS or localhost) and the outbox.
    if ("serviceWorker" in navigator && window.isSecureContext && !App.demo) {
      navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("Offline copy not available:", e));
    }
    // Ask the browser not to delete this site's saved data (granted more readily to installed apps).
    if (navigator.storage && navigator.storage.persist) {
      navigator.storage.persisted().then((yes) => yes || navigator.storage.persist()).catch(() => null);
    }
    if (App.user.role === "STAFF") offerInstall();
    renderOfflineBar();
    App.flushOutbox();
    setInterval(() => { if (readBox().length) App.flushOutbox(); }, 30000);
    // A known clock offset is good enough to show the page now; it is corrected in the background.
    if (savedClock.station !== undefined && !App.demo) syncClock().then(tick); else await syncClock();   // training: paused clock
    tick();
    setInterval(tick, 1000);
    setInterval(syncClock, 5 * 60 * 1000);
    if (App.user.role === "MANAGER") {
      App.refreshAlertCount();
      if (!onPasswordPage) managerPrompts();
    }
    return App.user;
  };

  /** Wrap async page code so failures are shown instead of silently breaking. */
  App.run = (fn) => fn().catch((err) => {
    if (err && err.message === "Please log in") return;
    console.error(err);
    App.error(err);
  });
})();
