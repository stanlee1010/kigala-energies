/* WARNINGS — open and resolved alerts */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Warnings" });
  const page = App.$("#page");
  const ICONS = { LOW_STOCK: "🛢️", STOCK_MISMATCH: "⚖️", METER_ERROR: "🔢", MISSING_SHIFT: "🕒", DEPOSIT_DIFFERENCE: "💵",
    REFILL_NOT_RECONCILED: "🚚", MISSING_STOCK_UPDATE: "🚚", FUEL_PRICE_UPDATE_REQUIRED: "⛽", MONTHLY_REVIEW_REQUIRED: "📊" };
  const LINKS = { LOW_STOCK: "/manager/stock.html", STOCK_MISMATCH: "/manager/shifts.html?shift=", METER_ERROR: "/manager/shifts.html?shift=",
    MISSING_SHIFT: "/manager/shifts.html?shift=", DEPOSIT_DIFFERENCE: "/manager/shifts.html?shift=", REFILL_NOT_RECONCILED: "/manager/shifts.html?shift=",
    MISSING_STOCK_UPDATE: "/manager/shifts.html?shift=", FUEL_PRICE_UPDATE_REQUIRED: "/manager/prices.html", MONTHLY_REVIEW_REQUIRED: "/dashboard.html" };

  page.innerHTML = `
    <div class="page-head"><div><h1>Warnings</h1><p>Condition warnings (low stock, unreconciled refills) clear automatically once fixed.</p></div></div>
    <div class="tabs" id="tabs"><button class="tab active" data-r="0">Open</button><button class="tab" data-r="1">Resolved</button></div>
    <div id="list" class="alert-list"></div>`;
  let resolved = "0";

  async function load() {
    const res = await App.get(`/api/alerts?resolved=${resolved}`);
    App.$("#list").innerHTML = res.alerts.map((a) => {
      const link = LINKS[a.alert_type] ? LINKS[a.alert_type] + (LINKS[a.alert_type].endsWith("=") ? (a.shift_id || "") : "") : null;
      return `<div class="alert-item alert-item--${a.severity}">
        <div class="alert-item__icon">${ICONS[a.alert_type] || "⚠️"}</div>
        <div class="alert-item__body">
          <div class="alert-item__title">⚠️ ${App.esc(a.title)}</div>
          <div class="alert-item__msg">${App.esc(a.message)}</div>
          <div class="alert-item__meta">${App.badge(a.severity)} ${App.label(a.alert_type)} · ${App.datetime(a.created_at)}
            ${a.is_resolved ? ` · Resolved ${App.datetime(a.resolved_at)} by ${App.esc(a.resolved_by_name || "system")}: ${App.esc(a.resolution_note || "")}` : ""}</div>
          <div class="row" style="margin-top:.4rem">
            ${link && !(link.endsWith("=")) ? `<a class="btn btn--sm" href="${link}">Go to issue</a>` : ""}
            ${!a.is_resolved && a.alert_type !== "FUEL_PRICE_UPDATE_REQUIRED" ? `<button class="btn btn--sm btn--primary" data-resolve="${a.id}">Mark resolved</button>` : ""}
          </div>
        </div></div>`;
    }).join("") || `<div class="empty">${resolved === "0" ? "✅ No open warnings." : "No resolved warnings yet."}</div>`;
  }

  App.on(App.$("#tabs"), "click", ".tab", (e, t) => {
    App.$$("#tabs .tab").forEach((x) => x.classList.toggle("active", x === t));
    resolved = t.dataset.r;
    App.run(load);
  });
  App.on(page, "click", "[data-resolve]", (e, b) => App.run(async () => {
    const note = await App.ask({ title: "Resolve warning", message: "Describe what was checked or done.", confirmLabel: "Resolve", input: { label: "Resolution note", required: true } });
    if (!note) return;
    await App.post(`/api/alerts/${b.dataset.resolve}/resolve`, { note });
    App.toast("Warning resolved.", "ok");
    load(); App.refreshAlertCount();
  }));
  await load();
});
