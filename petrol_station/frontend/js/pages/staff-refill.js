/* ==========================================================================
   FUEL REFILL ENTRY -> CONFIRM FUEL REFILL -> locked
   The record is created as AWAITING_CONFIRMATION; only "YES — CONFIRM"
   makes it final. "NO" returns it to the form for correction.
   ========================================================================== */
App.run(async () => {
  await App.init({ title: "Fuel Refill Entry" });

  const form = App.$("#refill-form");
  const steps = { form: App.$("#step-form"), confirm: App.$("#step-confirm"), done: App.$("#step-done") };
  let refill = null; // the saved, unconfirmed refill

  const show = (name) => {
    Object.entries(steps).forEach(([k, el]) => el.classList.toggle("hidden", k !== name));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const [{ fuel_types }, { suppliers }, { tanks }] = await Promise.all([
    App.get("/api/fuel-types"), App.get("/api/suppliers"), App.get("/api/tanks")]);
  App.$("#fuel_type_id").innerHTML = App.options(fuel_types.filter((f) => f.is_active), "id", "name", null, "Choose fuel…");

  // Tank choice only matters when a fuel has more than one tank.
  const syncTanks = (selected) => {
    const fuelTanks = tanks.filter((t) => String(t.fuel_type_id) === App.$("#fuel_type_id").value);
    App.$("#tank_id").innerHTML = App.options(fuelTanks, "id", "name", selected ?? (fuelTanks[0] || {}).id);
    App.$("#tank_id").required = fuelTanks.length > 1;
    App.$("#tank-field").classList.toggle("hidden", fuelTanks.length <= 1);
  };
  App.$("#fuel_type_id").addEventListener("change", () => syncTanks());
  App.$("#suppliers").innerHTML = suppliers.filter((s) => s.is_active).map((s) => `<option value="${App.esc(s.name)}">`).join("");

  // Default to the server's current date/time (not the phone's clock).
  const now = App.serverNow().toISOString();
  App.$("#refill_date").value = now.slice(0, 10);
  App.$("#refill_time").value = now.slice(11, 16);
  App.$("#refill_date").max = now.slice(0, 10);

  const updateTotal = () => {
    const q = Number(App.$("#quantity_litres").value), c = Number(App.$("#cost_per_litre").value);
    App.$("#total-cost").textContent = q > 0 && c > 0 ? App.money(q * c) : "—";
  };
  form.addEventListener("input", updateTotal);

  function fill(r) {
    App.$("#fuel_type_id").value = r.fuel_type_id;
    syncTanks(r.tank_id);
    App.$("#quantity_litres").value = r.quantity_litres;
    App.$("#refill_date").value = r.refill_date;
    App.$("#refill_time").value = String(r.refill_time).slice(0, 5);
    App.$("#supplier_name").value = r.supplier || "";
    App.$("#invoice_number").value = r.invoice_number || "";
    App.$("#delivery_reference").value = r.delivery_reference || "";
    App.$("#cost_per_litre").value = r.cost_per_litre;
    App.$("#notes").value = r.notes || "";
    updateTotal();
  }

  function summaryHtml(c) {
    return `
      <dt>Fuel type</dt><dd>${App.esc(c.fuel_type)}</dd>
      ${c.tank ? `<dt>Tank</dt><dd>${App.esc(c.tank)}</dd>` : ""}
      <dt>Quantity</dt><dd class="big">${App.litres(c.quantity_litres)}</dd>
      <dt>Cost/Litre</dt><dd>${App.money(c.cost_per_litre)}</dd>
      <dt>Total Cost</dt><dd class="big">${App.money(c.total_cost)}</dd>
      <dt>Supplier</dt><dd>${App.esc(c.supplier || "—")}</dd>
      <dt>Invoice</dt><dd>${App.esc(c.invoice_number || "—")}</dd>
      <dt>Date</dt><dd>${App.date(c.date)}</dd>
      <dt>Time</dt><dd>${App.esc(c.time)}</dd>`;
  }

  // Continue editing an unconfirmed refill: /staff/refill.html?edit=ID
  const editId = new URLSearchParams(location.search).get("edit");
  if (editId) {
    const { refill: r } = await App.get(`/api/refills/${editId}`);
    if (r.locked) {
      App.toast("This refill is already confirmed and locked.", "error");
    } else {
      refill = r;
      fill(r);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!Validation.validate(form)) return;
    App.run(async () => {
      const data = App.formData(form);
      const res = refill ? await App.put(`/api/refills/${refill.id}`, data) : await App.post("/api/refills", data);
      refill = res.refill;
      App.$("#confirm-list").innerHTML = summaryHtml(res.confirm);
      show("confirm");
    });
  });

  App.$("#no-btn").addEventListener("click", () => App.run(async () => {
    const res = await App.post(`/api/refills/${refill.id}/confirm`, { answer: "NO" });
    refill = res.refill;
    fill(refill);
    show("form");
    App.toast("Please correct the refill details, then continue.", "info");
    App.$("#quantity_litres").focus();
  }));

  App.$("#yes-btn").addEventListener("click", () => App.run(async () => {
    const btn = App.$("#yes-btn");
    btn.disabled = true;
    try {
      const res = await App.post(`/api/refills/${refill.id}/confirm`, { answer: "YES" });
      const r = res.refill;
      App.$("#done-list").innerHTML = summaryHtml({
        fuel_type: r.fuel, tank: r.tank, quantity_litres: r.quantity_litres, cost_per_litre: r.cost_per_litre, total_cost: r.total_cost,
        supplier: r.supplier, invoice_number: r.invoice_number, date: r.refill_date, time: String(r.refill_time).slice(0, 5),
      }) + `<dt>Confirmed by</dt><dd>${App.esc(r.confirmed_by_name)}</dd><dt>Confirmed at</dt><dd>${App.datetime(r.confirmed_at)}</dd>`;
      refill = null;
      show("done");
    } finally { btn.disabled = false; }
  }));
});
