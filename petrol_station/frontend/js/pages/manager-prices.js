/* FUEL PRICES — only the manager changes prices; full history kept */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Fuel Prices" });
  const page = App.$("#page");

  async function load() {
    const res = await App.get("/api/prices");
    page.innerHTML = `
      <div class="page-head"><div><h1>Fuel prices</h1><p>Selling prices per litre. Every change is kept in the price history.</p></div>
        ${res.price_review_required ? '<button class="btn btn--warn" id="review-btn">⛽ Complete price review</button>' : ""}</div>
      <div class="grid-3">${res.current.map((p) => `
        <div class="card">
          <div class="kpi__label">${App.fuelDot(p.color)}CURRENT ${App.esc(p.fuel.toUpperCase())} PRICE</div>
          <div class="kpi__value">${p.price_per_litre ? App.money(p.price_per_litre) + " / L" : '<span class="text-danger">Not set</span>'}</div>
          <div class="kpi__sub">${p.since ? "Since " + App.datetime(p.since) : ""}</div>
          <form class="form-actions" data-fuel="${p.fuel_type_id}" novalidate>
            <div class="field" style="flex:1"><label class="small">New price per litre</label>
              <input class="input" type="number" min="0" step="0.01" name="price_per_litre" data-positive required></div>
            <div class="field" style="flex:1 1 100%"><input class="input input--sm" name="note" placeholder="Note (optional)"></div>
            <button class="btn btn--primary" type="submit">UPDATE PRICE</button>
          </form>
        </div>`).join("")}</div>
      <div class="card"><h2>Price history</h2>${App.table({
        columns: [
          { key: "created_at", label: "Date / time", render: (r) => App.datetime(r.created_at) },
          { key: "fuel", label: "Fuel" },
          { key: "previous_price", label: "Previous price", cls: "num", render: (r) => App.money(r.previous_price) },
          { key: "price_per_litre", label: "New price", cls: "num", render: (r) => App.money(r.price_per_litre) },
          { key: "confirmation_status", label: "Decision", render: (r) => App.badge(r.confirmation_status === "KEPT" ? "info" : "ok", r.confirmation_status === "KEPT" ? "Kept current price" : App.label(r.confirmation_status)) },
          { key: "set_by_name", label: "Manager" },
          { key: "note", label: "Note", render: (r) => App.esc(r.note || "") },
        ], rows: res.history, empty: "No prices recorded yet." })}</div>`;

    App.$$("form[data-fuel]", page).forEach((form) => form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!Validation.validate(form)) return;
      App.run(async () => {
        const d = App.formData(form);
        if (!(await App.ask({ title: "Change fuel price", message: `Set the new price to <strong>${App.money(d.price_per_litre)}</strong> per litre? It applies to readings closed from now on.`, confirmLabel: "Update price" }))) return;
        await App.post("/api/prices", { fuel_type_id: Number(form.dataset.fuel), ...d });
        App.toast("Price updated.", "ok");
        load();
      });
    }));
    const review = App.$("#review-btn");
    if (review) review.addEventListener("click", async () => App.showPriceReview(await App.get("/api/periods/status"), load));
  }
  await load();
});
