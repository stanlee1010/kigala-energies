/* CAPITAL (manager only) — profit from the selling price and the buying price of fuel. */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Capital" });
  const page = App.$("#page");
  const pct = (v) => (v === null || v === undefined ? "—" : `${App.num(v, 1)}%`);
  const profitClass = (v) => (Number(v) < 0 ? "text-danger" : "text-ok");

  page.innerHTML = `
    <div class="page-head"><div><h1>💰 Capital</h1>
      <p>Profit from the selling price and the buying price of fuel. The buying price is the average cost of the fuel in your tanks, from the real delivery prices.</p></div></div>
    <section id="per-fuel"></section>
    <div class="card">
      <div class="card__head"><h2>Profit made</h2>
        <form id="range" class="row">
          <input class="input input--sm" type="date" name="start" aria-label="From">
          <input class="input input--sm" type="date" name="end" aria-label="To">
          <button class="btn btn--sm" type="submit">Show</button>
        </form></div>
      <div id="period"></div>
    </div>
    <div class="card">
      <h2>Profit calculator</h2>
      <p class="small muted">Work out the capital needed and the profit before buying fuel.</p>
      <form id="calc" novalidate><div class="form-grid">
        <div class="field"><label>Fuel (fills in the prices)</label><select class="input" id="calc-fuel"></select></div>
        <div class="field"><label>Litres</label><input class="input input--big" type="number" inputmode="decimal" min="0" step="0.001" name="litres" value="10000" data-positive required></div>
        <div class="field"><label>Buying price per litre</label><input class="input input--big" type="number" inputmode="decimal" min="0" step="0.01" name="buying_price" data-positive required></div>
        <div class="field"><label>Selling price per litre</label><input class="input input--big" type="number" inputmode="decimal" min="0" step="0.01" name="selling_price" data-positive required></div>
      </div><div class="form-actions"><button class="btn btn--primary btn--lg" type="submit">Calculate</button></div></form>
      <div id="calc-result"></div>
    </div>`;

  let fuels = [];

  function renderFuels(d) {
    App.$("#per-fuel").innerHTML = `<div class="grid-2">${d.fuels.map((f) => `
      <div class="card" style="border-top:4px solid ${App.esc(f.color)}">
        <div class="card__head"><h2>${App.fuelDot(f.color)}${App.esc(f.fuel)}</h2>
          ${f.margin_percent !== null ? `<span class="badge ${Number(f.profit_per_litre) < 0 ? "badge--danger" : "badge--ok"}">margin ${pct(f.margin_percent)}</span>` : ""}</div>
        <table class="pl-table">
          <tr><td>Selling price per litre</td><td>${f.selling_price !== null ? App.money(f.selling_price) : '<span class="text-danger">not set</span>'}</td></tr>
          <tr><td>− Buying price per litre <span class="small muted">(average cost in stock)</span></td>
              <td>${f.buying_price !== null ? App.money(f.buying_price) : '<span class="text-danger">no cost yet</span>'}</td></tr>
          <tr class="total ${profitClass(f.profit_per_litre)}"><td>= Profit per litre</td><td>${f.profit_per_litre !== null ? App.money(f.profit_per_litre) : "—"}</td></tr>
        </table>
        ${f.last_buying_price !== null ? `<p class="small muted">Last delivery: ${App.money(f.last_buying_price)} per litre on ${App.date(f.last_buying_date)}.</p>` : ""}
        <table class="pl-table">
          <tr><td>Fuel in the tanks</td><td>${App.litres(f.litres_in_tanks)}</td></tr>
          <tr><td>Capital in stock <span class="small muted">(litres × buying price)</span></td><td>${App.money(f.capital_in_stock)}</td></tr>
          <tr><td>Value at the selling price</td><td>${App.money(f.value_at_selling_price)}</td></tr>
          <tr class="total ${profitClass(f.expected_profit)}"><td>Expected profit when sold</td><td>${App.money(f.expected_profit)}</td></tr>
        </table>
        ${f.buying_price === null ? '<p class="small text-danger">Record the tank opening balance (with its cost) or confirm a refill to get a buying price.</p>' : ""}
      </div>`).join("")}</div>
      <div class="kpis" style="margin-top:1rem">
        <div class="kpi"><div class="kpi__label">Total capital in stock</div><div class="kpi__value">${App.money(d.totals.capital_in_stock)}</div></div>
        <div class="kpi"><div class="kpi__label">Stock value at selling prices</div><div class="kpi__value">${App.money(d.totals.value_at_selling_price)}</div></div>
        <div class="kpi ${Number(d.totals.expected_profit) < 0 ? "kpi--loss" : "kpi--profit"}"><div class="kpi__label">Expected profit on stock</div><div class="kpi__value">${App.money(d.totals.expected_profit)}</div></div>
      </div>`;
  }

  function renderPeriod(p) {
    App.$('#range [name="start"]').value = p.start;
    App.$('#range [name="end"]').value = p.end;
    App.$("#period").innerHTML = `<p class="small muted">${p.label ? `${App.esc(p.label)} reporting period · ` : ""}${App.date(p.start)} – ${App.date(p.end)}. Profit = sales − buying cost of the litres sold (before expenses).</p>` +
      App.table({
        columns: [
          { key: "fuel", label: "Fuel", render: (r) => App.fuelDot(r.color) + App.esc(r.fuel) },
          { key: "litres_sold", label: "Litres sold", cls: "num", render: (r) => App.litres(r.litres_sold) },
          { key: "sales", label: "Sales (selling price)", cls: "num", render: (r) => App.money(r.sales) },
          { key: "cost_of_fuel_sold", label: "− Buying cost", cls: "num", render: (r) => App.money(r.cost_of_fuel_sold) },
          { key: "profit", label: "= Profit", cls: "num", render: (r) => `<strong class="${profitClass(r.profit)}">${App.money(r.profit)}</strong>` },
          { key: "profit_per_litre", label: "Per litre", cls: "num", render: (r) => App.money(r.profit_per_litre) },
          { key: "margin_percent", label: "Margin", cls: "num", render: (r) => pct(r.margin_percent) },
        ],
        rows: p.fuels, empty: "No fuel sold in these dates.",
        footer: p.fuels.length ? [{ html: "Total" }, { cls: "num", html: App.litres(p.litres_sold) }, { cls: "num", html: App.money(p.sales) },
          { cls: "num", html: App.money(p.cost_of_fuel_sold) }, { cls: "num", html: `<span class="${profitClass(p.profit)}">${App.money(p.profit)}</span>` },
          {}, { cls: "num", html: pct(p.margin_percent) }] : null,
      });
  }

  async function load(range = {}) {
    const d = await App.get("/api/capital" + App.qs(range));
    fuels = d.fuels;
    renderFuels(d);
    renderPeriod(d.period);
    const sel = App.$("#calc-fuel");
    if (!sel.options.length) {
      sel.innerHTML = App.options(fuels, "fuel_type_id", "fuel");
      fillPrices();
    }
  }

  function fillPrices() {
    const f = fuels.find((x) => String(x.fuel_type_id) === App.$("#calc-fuel").value);
    if (!f) return;
    const form = App.$("#calc");
    App.$('[name="buying_price"]', form).value = f.last_buying_price ?? f.buying_price ?? "";
    App.$('[name="selling_price"]', form).value = f.selling_price ?? "";
  }

  App.$("#calc-fuel").addEventListener("change", fillPrices);
  App.$("#range").addEventListener("submit", (e) => { e.preventDefault(); App.run(() => load(App.formData(e.target))); });
  App.$("#calc").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!Validation.validate(e.target)) return;
    App.run(async () => {
      const r = await App.post("/api/capital/calculate", App.formData(e.target));
      App.$("#calc-result").innerHTML = `<table class="pl-table" style="margin-top:1rem">
        <tr><td>Capital needed to buy ${App.litres(r.litres)} <span class="small muted">(litres × ${App.money(r.buying_price)})</span></td><td>${App.money(r.capital_needed)}</td></tr>
        <tr><td>Sales at ${App.money(r.selling_price)} per litre</td><td>${App.money(r.sales)}</td></tr>
        <tr class="total ${profitClass(r.profit)}"><td>= ${r.is_loss ? "Loss" : "Profit"}</td><td>${App.money(Math.abs(Number(r.profit)))}</td></tr>
        <tr><td>Profit per litre</td><td>${App.money(r.profit_per_litre)}</td></tr>
        <tr><td>Margin (profit ÷ sales)</td><td>${pct(r.margin_percent)}</td></tr>
        <tr><td>Return on capital (profit ÷ capital)</td><td>${pct(r.return_on_capital_percent)}</td></tr>
      </table>`;
    });
  });

  await load();
});
