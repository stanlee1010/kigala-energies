/* DIRECTOR — sales and profit for one location or all locations: today, week, month, year + charts. */
App.run(async () => {
  await App.init({ role: "DIRECTOR", title: "Sales & Profit" });
  const page = App.$("#page");
  const PERIODS = [["today", "Today"], ["week", "This week"], ["month", "This month"], ["year", "This year"]];
  let station = "all";
  let granularity = "daily";
  const charts = {};

  page.innerHTML = `
    <div class="page-head"><div><h1>Sales &amp; profit</h1>
      <p>What has been sold and the profit made. Choose one location or all at once.</p></div>
      <label class="field" style="min-width:240px"><span class="small muted">Location</span>
        <select class="input" id="location"></select></label></div>
    <section class="grid-2" id="periods"></section>
    <div class="card" id="by-location-card">
      <h2>By location</h2><div id="by-location"></div></div>
    <h2 class="section-title">Trends</h2>
    <div class="tabs" id="chart-tabs">
      <button class="tab active" data-g="daily">Daily</button><button class="tab" data-g="weekly">Weekly</button>
      <button class="tab" data-g="monthly">Monthly</button><button class="tab" data-g="yearly">Yearly</button></div>
    <section class="chart-grid">
      <div class="card"><h3>Sales</h3><div class="chart-box"><canvas role="img" id="c-sales" aria-label="Sales chart (the same figures are in the table below)"></canvas></div></div>
      <div class="card"><h3>Profit / loss</h3><div class="chart-box"><canvas role="img" id="c-profit" aria-label="Profit and loss chart (the same figures are in the table below)"></canvas></div></div>
      <div class="card"><h3>Fuel sold (litres)</h3><div class="chart-box"><canvas role="img" id="c-fuel" aria-label="Fuel sold chart (the same figures are in the table below)"></canvas></div></div>
      <div class="card"><h3>Expenses</h3><div class="chart-box"><canvas role="img" id="c-exp" aria-label="Expenses chart (the same figures are in the table below)"></canvas></div></div>
    </section>
    <details class="card"><summary><strong>Show chart data as a table</strong></summary><div id="chart-table" style="margin-top:.75rem"></div></details>`;

  const profitCls = (v) => (Number(v) < 0 ? "text-danger" : "text-ok");

  function periodCard(key, label, p) {
    return `<div class="card">
      <div class="card__head"><h2>${label}</h2>
        <span class="badge ${p.is_loss ? "badge--danger" : "badge--ok"}">${p.is_loss ? "Loss" : "Profit"}</span></div>
      <div class="kpis">
        <div class="kpi"><div class="kpi__label">Sales</div><div class="kpi__value">${App.money(p.revenue)}</div></div>
        <div class="kpi"><div class="kpi__label">Fuel sold</div><div class="kpi__value">${App.litres(p.fuel_sold_litres)}</div></div>
        <div class="kpi ${p.is_loss ? "kpi--loss" : "kpi--profit"}"><div class="kpi__label">${p.is_loss ? "Net loss" : "Net profit"}</div>
          <div class="kpi__value">${App.money(Math.abs(Number(p.net_profit)))}</div></div>
      </div>
      <table class="pl-table">
        ${p.by_fuel.map((f) => `<tr class="small"><td>${App.fuelDot(f.color)}${App.esc(f.fuel)}: ${App.litres(f.litres)}</td><td>${App.money(f.sales)}</td></tr>`).join("")}
        <tr><td>Sales</td><td>${App.money(p.revenue)}</td></tr>
        <tr><td>− Cost of the fuel sold</td><td>${App.money(p.cost_of_fuel_sold)}</td></tr>
        <tr><td>= Gross profit</td><td>${App.money(p.gross_profit)}</td></tr>
        <tr><td>− Expenses</td><td>${App.money(p.operating_expenses)}</td></tr>
        <tr class="total ${profitCls(p.net_profit)}"><td>= ${p.is_loss ? "Net loss" : "Net profit"}</td><td>${App.money(Math.abs(Number(p.net_profit)))}</td></tr>
      </table></div>`;
  }

  async function loadOverview() {
    const d = await App.get(`/api/director/overview${App.qs({ station })}`);
    const sel = App.$("#location");
    if (!sel.options.length) {
      sel.innerHTML = `<option value="all">All locations (${d.locations.length})</option>` + App.options(d.locations, "id", "name");
    }
    App.$("#periods").innerHTML = PERIODS.map(([k, label]) => periodCard(k, label, d.periods[k])).join("");
    App.$("#by-location-card").classList.toggle("hidden", station !== "all");
    App.$("#by-location").innerHTML = App.table({
      columns: [
        { key: "name", label: "Location", render: (r) => `<strong>${App.esc(r.name)}</strong>` },
        ...PERIODS.flatMap(([k, label]) => [
          { key: `${k}_s`, label: `${label} sales`, cls: "num", render: (r) => App.money(r[k].revenue) },
          { key: `${k}_p`, label: `${label} profit`, cls: "num", render: (r) => `<span class="${profitCls(r[k].net_profit)}">${App.money(r[k].net_profit)}</span>` },
        ]),
      ],
      rows: d.by_location, cards: false,
      footer: [{ html: "All locations" }, ...PERIODS.flatMap(([k]) => [
        { cls: "num", html: App.money(d.periods[k].revenue) },
        { cls: "num", html: `<span class="${profitCls(d.periods[k].net_profit)}">${App.money(d.periods[k].net_profit)}</span>` }])],
    });
  }

  function bar(id, labels, values, { money = true, diverging = false } = {}) {
    if (typeof Chart === "undefined") return;
    const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    const brand = css("--brand") || "#0f766e";
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(document.getElementById(id), {
      type: "bar",
      data: { labels, datasets: [{ data: values.map(Number), borderRadius: 4, borderSkipped: "start", maxBarThickness: 28,
        backgroundColor: diverging ? values.map((v) => (Number(v) < 0 ? "#dc2626" : brand)) : brand }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => (money ? App.money(c.parsed.y) : App.litres(c.parsed.y)) } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: css("--muted"), maxRotation: 0, autoSkip: true } },
          y: { grid: { color: css("--border") }, border: { display: false },
               ticks: { color: css("--muted"), callback: (v) => new Intl.NumberFormat("en-US", { notation: "compact" }).format(v) } },
        },
      },
    });
  }

  async function loadCharts() {
    const s = await App.get(`/api/director/charts${App.qs({ station, granularity })}`);
    bar("c-sales", s.labels, s.revenue);
    bar("c-profit", s.labels, s.profit, { diverging: true });
    bar("c-fuel", s.labels, s.fuel_sold, { money: false });
    bar("c-exp", s.labels, s.expenses);
    App.$("#chart-table").innerHTML = App.table({
      columns: [
        { key: "l", label: "Period" },
        { key: "r", label: "Sales", cls: "num", render: (r) => App.money(r.r) },
        { key: "f", label: "Fuel sold", cls: "num", render: (r) => App.litres(r.f) },
        { key: "e", label: "Expenses", cls: "num", render: (r) => App.money(r.e) },
        { key: "p", label: "Profit/loss", cls: "num", render: (r) => `<span class="${profitCls(r.p)}">${App.money(r.p)}</span>` },
      ],
      rows: s.labels.map((l, i) => ({ l, r: s.revenue[i], f: s.fuel_sold[i], e: s.expenses[i], p: s.profit[i] })),
    });
  }

  App.$("#location").addEventListener("change", (e) => {
    station = e.target.value;
    App.run(async () => { await loadOverview(); await loadCharts(); });
  });
  App.on(App.$("#chart-tabs"), "click", ".tab", (e, t) => {
    App.$$("#chart-tabs .tab").forEach((x) => x.classList.toggle("active", x === t));
    granularity = t.dataset.g;
    App.run(loadCharts);
  });

  await loadOverview();
  await loadCharts();
  setInterval(() => App.run(loadOverview), 60 * 1000);
});
