/* TANKS & STOCK (manager only) — tank levels, number of tanks, per-shift movement per pump,
   low-stock levels, opening balance / adjustments and stock history. */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Tanks & Stock" });
  const page = App.$("#page");
  let fuels = [], tanks = [], allTanks = [];

  page.innerHTML = `
    <div class="page-head"><div><h1>Tanks &amp; stock</h1>
      <p>Tank levels are calculated automatically: opening + refills − litres sold at the pumps. Only you can see them.</p></div>
      <button class="btn btn--primary" id="adjust-btn">+ Opening balance / adjustment</button></div>
    <section id="tanks" class="stock-grid"></section>
    <div class="card"><h2>Number of tanks</h2>
      <p class="small muted">Each fuel has at least one tank. A tank can only be removed when it is empty.</p>
      <div id="tank-counts" class="grid-3"></div></div>
    <div class="card"><div class="card__head"><h2>Tank movement in a shift</h2>
        <select class="input input--sm" id="shift-pick" style="max-width:260px"></select></div>
      <p class="small muted">The tank is the shared stock; each pump has its own meter. Expected tank balance = Opening + Refills − total sold by all its pumps.</p>
      <div id="breakdown"></div></div>
    <div class="card"><h2>Low-stock alert levels</h2><p class="small muted">A warning is raised when a fuel reaches its minimum.</p><div id="thresholds" class="grid-3"></div></div>
    <div class="card"><h2>Adjustments</h2><div id="adjustments"></div></div>
    <div class="card"><h2>Stock per shift (by fuel)</h2><div id="history"></div></div>`;

  function renderTanks() {
    App.$("#tanks").innerHTML = tanks.map((t) => {
      const pct = Math.max(0, Math.min(100, t.percent_full));
      return `<div class="gauge ${t.is_low ? "gauge--low" : ""}">
        <div class="gauge__head"><strong>${App.fuelDot(t.color)}${App.esc(t.name)}</strong>
          <button class="btn btn--sm" data-edit-tank="${t.id}">Edit</button></div>
        <div class="gauge__value">${App.litres(t.litres)}</div>
        <div class="gauge__bar"><div class="gauge__fill" style="width:${pct}%;background:${t.is_low ? "#dc2626" : App.esc(t.color)}"></div></div>
        <div class="small muted">${App.num(pct, 0)}% of ${App.litres(t.capacity_litres)} · space for ${App.litres(t.space_litres)}</div>
        <div class="small muted">Pumps: ${App.esc(t.pumps.join(", ") || "none")}</div>
        ${Number(t.litres) < 0
          ? '<div class="small text-danger"><strong>More fuel sold than recorded in this tank — press “+ Opening balance / adjustment” and record what was in it at the start.</strong></div>'
          : t.is_low ? `<div class="small text-danger"><strong>LOW — minimum ${App.litres(t.minimum_litres)}</strong></div>` : ""}
      </div>`;
    }).join("") || '<div class="empty">No tanks.</div>';

    App.$("#tank-counts").innerHTML = fuels.filter((f) => f.is_active).map((f) => {
      const n = tanks.filter((t) => t.fuel_type_id === f.id).length;
      return `<form class="kpi" data-tank-count="${f.id}" data-current="${n}" novalidate>
        <div class="kpi__label">${App.fuelDot(f.color)}${App.esc(f.name)} tanks</div>
        <div class="row" style="margin-top:.4rem">
          <button type="button" class="btn" data-step="-1" aria-label="One less ${App.esc(f.name)} tank">−</button>
          <input class="input input--big center" style="width:5.5rem" type="number" min="1" max="10" step="1" name="count" value="${n}" required>
          <button type="button" class="btn" data-step="1" aria-label="One more ${App.esc(f.name)} tank">+</button>
          <button type="submit" class="btn btn--primary">Save</button>
        </div></form>`;
    }).join("");
  }

  async function loadBreakdown() {
    const id = App.$("#shift-pick").value;
    if (!id) { App.$("#breakdown").innerHTML = '<div class="empty">No shifts yet.</div>'; return; }
    const res = await App.get(`/api/tanks/shift/${id}`);
    App.$("#breakdown").innerHTML = res.tanks.length
      ? `<div class="grid-2">${res.tanks.map(App.tankCard).join("")}</div>`
      : '<div class="empty">No tank movement in this shift.</div>';
  }

  async function load() {
    const [ft, tk, hist, adj, sh] = await Promise.all([App.get("/api/fuel-types"), App.get("/api/tanks"),
      App.get("/api/stock/history"), App.get("/api/stock/adjustments"), App.get("/api/shifts")]);
    fuels = ft.fuel_types; tanks = tk.tanks; allTanks = tk.all;
    renderTanks();

    const pick = App.$("#shift-pick");
    const previous = pick.value;
    pick.innerHTML = App.options(sh.shifts.filter((s) => s.status !== "SCHEDULED"), "id", (s) => `${s.label} (${App.label(s.status)})`);
    if (previous && [...pick.options].some((o) => o.value === previous)) pick.value = previous;
    await loadBreakdown();

    App.$("#thresholds").innerHTML = fuels.filter((f) => f.is_active).map((f) => `
      <form class="field" data-threshold="${f.id}"><label>${App.fuelDot(f.color)}${App.esc(f.name)} minimum (L)</label>
        <div class="row"><input class="input" type="number" min="0" step="1" name="minimum_litres" value="${f.minimum_litres ?? ""}" required style="flex:1">
        <button class="btn" type="submit">Save</button></div></form>`).join("");
    const tankName = Object.fromEntries(allTanks.map((t) => [t.id, t.name]));
    App.$("#adjustments").innerHTML = App.table({
      columns: [
        { key: "adjusted_at", label: "When", render: (r) => App.localDateTime(r.adjusted_at) },
        { key: "tank", label: "Tank", render: (r) => App.esc(tankName[r.tank_id] || r.fuel) },
        { key: "adjustment_type", label: "Type", render: (r) => App.label(r.adjustment_type) },
        { key: "quantity_litres", label: "Litres", cls: "num", render: (r) => App.signed(r.quantity_litres, App.litres) },
        { key: "cost_per_litre", label: "Cost/L", cls: "num", render: (r) => App.money(r.cost_per_litre) },
        { key: "reason", label: "Reason" },
      ], rows: adj.adjustments, empty: "No adjustments.",
    });
    App.$("#history").innerHTML = App.table({
      columns: [
        { key: "shift_label", label: "Shift" },
        { key: "fuel", label: "Fuel" },
        { key: "opening_stock", label: "Opening", cls: "num", render: (r) => App.litres(r.opening_stock) },
        { key: "refilled_quantity", label: "+ Refilled", cls: "num", render: (r) => App.litres(r.refilled_quantity) },
        { key: "fuel_sold", label: "− Sold", cls: "num", render: (r) => App.litres(r.fuel_sold) },
        { key: "calculated_closing_stock", label: "= Closing", cls: "num", render: (r) => `<strong>${App.litres(r.calculated_closing_stock)}</strong>` },
        { key: "a", label: "", cls: "actions", render: (r) => `<button class="btn btn--sm" data-reconcile="${r.id}">Reconcile</button>` },
      ], rows: hist.rows, empty: "No shift stock records yet.",
    });
  }

  App.$("#shift-pick").addEventListener("change", () => App.run(loadBreakdown));

  App.on(page, "click", "[data-step]", (e, b) => {
    const input = App.$('[name="count"]', b.closest("form"));
    input.value = Math.max(1, Math.min(10, (Number(input.value) || 1) + Number(b.dataset.step)));
  });
  App.on(page, "submit", "[data-tank-count]", (e, form) => {
    e.preventDefault();
    if (!Validation.validate(form)) return;
    App.run(async () => {
      const fuel = fuels.find((f) => String(f.id) === form.dataset.tankCount);
      const current = Number(form.dataset.current), wanted = Number(App.$('[name="count"]', form).value);
      if (wanted === current) { App.toast(`${fuel.name} already has ${current} tank(s).`); return; }
      const res = await App.put(`/api/tanks/count/${fuel.id}`, { count: wanted });
      const parts = [];
      if (res.added.length) parts.push(`added ${res.added.join(", ")}`);
      if (res.removed.length) parts.push(`removed ${res.removed.join(", ")}`);
      App.toast(`${fuel.name}: ${res.tanks} tank(s) — ${parts.join("; ")}. Link pumps to tanks on the Pumps page.`, "ok", 7000);
      load();
    });
  });
  App.on(page, "click", "[data-edit-tank]", (e, b) => {
    const t = tanks.find((x) => String(x.id) === b.dataset.editTank);
    const form = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Tank name</label><input class="input" name="name" value="${App.esc(t.name)}" required></div>
      <div class="field"><label>Capacity (litres)</label><input class="input" type="number" min="1" step="1" name="capacity_litres" value="${Number(t.capacity_litres)}" required></div>
    </div></form>`);
    App.modal({ title: `Edit ${App.esc(t.name)}`, body: form, actions: [{ label: "Cancel" }, {
      label: "Save", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        await App.put(`/api/tanks/${t.id}`, App.formData(form));
        App.toast("Tank updated.", "ok");
        load();
      },
    }] });
  });
  App.on(page, "submit", "[data-threshold]", (e, form) => {
    e.preventDefault();
    App.run(async () => {
      await App.put(`/api/thresholds/${form.dataset.threshold}`, App.formData(form));
      App.toast("Minimum level saved.", "ok");
      load();
    });
  });
  App.on(page, "click", "[data-reconcile]", (e, b) => App.run(async () => {
    const reason = await App.ask({ title: "Reconcile stock record", message: "Recalculate this record from confirmed refills, readings and adjustments.", confirmLabel: "Reconcile", input: { label: "Reason", required: true } });
    if (!reason) return;
    await App.post(`/api/stock/${b.dataset.reconcile}/reconcile`, { reason });
    App.toast("Stock record reconciled.", "ok");
    load();
  }));

  App.$("#adjust-btn").addEventListener("click", () => {
    const form = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Tank</label><select class="input" name="tank_id" required>${App.options(tanks, "id", (t) => `${t.name} (${t.fuel})`)}</select></div>
      <div class="field"><label>Type</label><select class="input" name="adjustment_type">
        <option value="OPENING_BALANCE">Opening balance (go-live tank contents)</option>
        <option value="DIP_CORRECTION">Dip correction</option><option value="LOSS">Loss / evaporation</option>
        <option value="RECONCILIATION">Reconciliation</option><option value="OTHER">Other</option></select></div>
      <div class="field"><label>Litres (use − to remove)</label><input class="input" type="number" step="0.001" name="quantity_litres" required></div>
      <div class="field"><label>Cost per litre (for opening balance)</label><input class="input" type="number" min="0" step="0.01" name="cost_per_litre"></div>
      <div class="field span-all"><label>Reason</label><textarea class="input" name="reason" required></textarea></div>
    </div><p class="small muted">Opening balance sets what was in the tank at go-live and its cost (for weighted-average costing).</p></form>`);
    const typeSel = App.$('[name="adjustment_type"]', form);
    const sync = () => { App.$('[name="cost_per_litre"]', form).required = typeSel.value === "OPENING_BALANCE"; };
    typeSel.addEventListener("change", sync); sync();
    App.modal({ title: "Opening balance / stock adjustment", body: form, actions: [{ label: "Cancel" }, {
      label: "Save", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        const data = App.formData(form);
        data.fuel_type_id = tanks.find((t) => String(t.id) === data.tank_id).fuel_type_id;
        await App.post("/api/stock/adjustments", data);
        App.toast("Recorded.", "ok");
        load();
      },
    }] });
  });

  await load();
});
