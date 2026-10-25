/* FUEL REFILLS — review, approve, flag, correct (with full confirmation + audit history) */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Fuel Refills" });
  const page = App.$("#page");
  const params = new URLSearchParams(location.search);
  const STATUSES = ["", "DRAFT", "AWAITING_CONFIRMATION", "CONFIRMED", "APPROVED", "FLAGGED", "CORRECTED"];

  page.innerHTML = `
    <div class="page-head"><div><h1>Fuel refills</h1><p>Staff-confirmed refills are locked. Approve them, or correct them with a reason.</p></div>
      <a class="btn btn--primary" href="/staff/refill.html">+ Record refill</a></div>
    <div class="card">
      <form id="filters" class="filters">
        <div class="field"><label>Status</label><select class="input" name="status">${App.options(STATUSES.map((s) => ({ v: s, l: s ? App.label(s) : "All" })), "v", "l", params.get("status") || "")}</select></div>
        <div class="field"><label>From</label><input class="input" type="date" name="start"></div>
        <div class="field"><label>To</label><input class="input" type="date" name="end"></div>
        <button class="btn" type="submit">Show</button>
      </form>
      <div id="list"></div>
    </div>`;

  let rows = [], fuels = [];
  // Only used when correcting a refill: loaded alongside the page instead of before it.
  const fuelsReady = App.get("/api/fuel-types").then((r) => { fuels = r.fuel_types; });
  fuelsReady.catch(() => null);

  async function load() {
    ({ refills: rows } = await App.get("/api/refills" + App.qs(App.formData(App.$("#filters")))));
    const total = rows.reduce((a, r) => a + Number(r.quantity_litres), 0);
    App.$("#list").innerHTML = App.table({
      columns: [
        { key: "refill_date", label: "Date", render: (r) => `${App.date(r.refill_date)} ${String(r.refill_time).slice(0, 5)}` },
        { key: "fuel", label: "Fuel", render: (r) => App.fuelDot(r.fuel_color) + App.esc(r.fuel) },
        { key: "tank", label: "Tank", render: (r) => App.esc(r.tank || "—") },
        { key: "quantity_litres", label: "Quantity", cls: "num", render: (r) => App.litres(r.quantity_litres) },
        { key: "cost_per_litre", label: "Cost/L", cls: "num", render: (r) => App.money(r.cost_per_litre, false) },
        { key: "total_cost", label: "Total", cls: "num", render: (r) => App.money(r.total_cost) },
        { key: "supplier", label: "Supplier", render: (r) => App.esc(r.supplier || "—") },
        { key: "invoice_number", label: "Invoice" },
        { key: "recorded_by_name", label: "Recorded by" },
        { key: "status", label: "Status", render: (r) => App.badge(r.status) },
        { key: "a", label: "", cls: "actions", render: (r) => `<button class="btn btn--sm btn--primary" data-open="${r.id}">Review</button>` },
      ],
      rows, empty: "No refills match.",
      footer: rows.length ? [{ html: "Total" }, {}, {}, { cls: "num", html: App.litres(total) }, {}, { cls: "num", html: App.money(rows.reduce((a, r) => a + Number(r.total_cost), 0)) }, {}, {}, {}, {}, {}] : null,
    });
  }

  async function open(id) {
    const { refill: r } = await App.get(`/api/refills/${id}`);
    const body = `
      <dl class="confirm-list">
        <dt>Fuel</dt><dd>${App.esc(r.fuel)}</dd><dt>Tank</dt><dd>${App.esc(r.tank || "—")}</dd><dt>Quantity</dt><dd class="big">${App.litres(r.quantity_litres)}</dd>
        <dt>Cost / L</dt><dd>${App.money(r.cost_per_litre)}</dd><dt>Total cost</dt><dd>${App.money(r.total_cost)}</dd>
        <dt>Date / time</dt><dd>${App.date(r.refill_date)} ${String(r.refill_time).slice(0, 5)}</dd><dt>Shift</dt><dd>${App.esc(r.shift_label || "—")}</dd>
        <dt>Supplier</dt><dd>${App.esc(r.supplier || "—")}</dd><dt>Invoice</dt><dd>${App.esc(r.invoice_number || "—")}</dd>
        <dt>Recorded by</dt><dd>${App.esc(r.recorded_by_name || "—")}</dd>
        <dt>Confirmed by</dt><dd>${App.esc(r.confirmed_by_name || "—")} ${r.confirmed_at ? "· " + App.datetime(r.confirmed_at) : ""}</dd>
        <dt>Approved by</dt><dd>${App.esc(r.approved_by_name || "—")} ${r.approved_at ? "· " + App.datetime(r.approved_at) : ""}</dd>
        <dt>Status</dt><dd>${App.badge(r.status)}</dd>
      </dl>
      ${r.notes ? `<p><strong>Staff notes:</strong> ${App.esc(r.notes)}</p>` : ""}
      ${r.manager_note ? `<p><strong>Manager note:</strong> ${App.esc(r.manager_note)}</p>` : ""}
      <h3>Confirmation log</h3>${App.table({ columns: [
        { key: "confirmed_at", label: "When", render: (c) => App.datetime(c.confirmed_at) },
        { key: "user", label: "User" },
        { key: "confirmation_result", label: "Result", render: (c) => App.label(c.confirmation_result) },
        { key: "entered_quantity", label: "Entered", cls: "num", render: (c) => App.litres(c.entered_quantity) },
        { key: "confirmed_quantity", label: "Confirmed", cls: "num", render: (c) => App.litres(c.confirmed_quantity) },
        { key: "ip_address", label: "IP" },
      ], rows: r.confirmations, empty: "Not confirmed yet." })}
      <h3 style="margin-top:1rem">Audit trail</h3>${App.table({ columns: [
        { key: "created_at", label: "When", render: (a) => App.datetime(a.created_at) },
        { key: "user", label: "User" }, { key: "action", label: "Action" },
        { key: "old_values", label: "Previous", render: (a) => `<code>${App.esc(a.old_values ? JSON.stringify(a.old_values) : "")}</code>` },
        { key: "new_values", label: "New", render: (a) => `<code>${App.esc(a.new_values ? JSON.stringify(a.new_values) : "")}</code>` },
        { key: "reason", label: "Why", render: (a) => App.esc(a.reason || "") },
      ], rows: r.audit, cards: false })}`;
    const actions = [{ label: "Close" }];
    const confirmed = ["CONFIRMED", "FLAGGED", "CORRECTED"].includes(r.status);
    if (confirmed) actions.push({ label: "✔ Approve", cls: "btn--ok", onClick: async () => { await App.post(`/api/refills/${r.id}/approve`); App.toast("Refill approved.", "ok"); load(); } });
    if (!["DRAFT", "AWAITING_CONFIRMATION"].includes(r.status)) {
      if (r.status !== "FLAGGED") actions.push({ label: "🚩 Flag", cls: "btn--danger", onClick: async () => {
        const reason = await App.ask({ title: "Flag refill", confirmLabel: "Flag", danger: true, input: { label: "Reason", required: true } });
        if (!reason) return false;
        await App.post(`/api/refills/${r.id}/flag`, { reason }); App.toast("Refill flagged.", "ok"); load();
      } });
      actions.push({ label: "Correct", cls: "btn--warn", onClick: () => { App.run(() => correct(r)); } });
    }
    App.modal({ title: `Refill #${r.id}`, body, wide: true, actions });
  }

  async function correct(r) {
    await fuelsReady;
    const form = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Fuel</label><select class="input" name="fuel_type_id">${App.options(fuels, "id", "name", r.fuel_type_id)}</select></div>
      <div class="field"><label>Quantity (L)</label><input class="input" type="number" min="0" step="0.001" name="quantity_litres" value="${r.quantity_litres}" data-positive required></div>
      <div class="field"><label>Cost per litre</label><input class="input" type="number" min="0" step="0.01" name="cost_per_litre" value="${r.cost_per_litre}" data-positive required></div>
      <div class="field"><label>Date</label><input class="input" type="date" name="refill_date" value="${r.refill_date}" required></div>
      <div class="field"><label>Time</label><input class="input" type="time" name="refill_time" value="${String(r.refill_time).slice(0, 5)}" required></div>
      <div class="field"><label>Invoice</label><input class="input" name="invoice_number" value="${App.esc(r.invoice_number || "")}" required></div>
      <div class="field span-all"><label>Reason for correction</label><textarea class="input" name="reason" required></textarea></div>
    </div><p class="small muted">The previous and new values, your name and the reason are stored permanently.</p></form>`);
    App.modal({ title: "Correct refill", body: form, actions: [{ label: "Cancel" }, {
      label: "Save correction", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        await App.post(`/api/refills/${r.id}/correct`, App.formData(form));
        App.toast("Refill corrected. Stock updated and audit logged.", "ok");
        load();
      },
    }] });
  }

  App.$("#filters").addEventListener("submit", (e) => { e.preventDefault(); App.run(load); });
  App.on(page, "click", "[data-open]", (e, b) => App.run(() => open(b.dataset.open)));
  await load();
});
