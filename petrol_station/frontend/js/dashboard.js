/* ==========================================================================
   Manager dashboard: today's figures, warnings, first-Wednesday review,
   stock levels and performance charts. Every number comes from the API.
   ========================================================================== */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Manager Dashboard" });

  const ALERT_ICONS = {
    LOW_STOCK: "🛢️", STOCK_MISMATCH: "⚖️", METER_ERROR: "🔢", MISSING_SHIFT: "🕒", DEPOSIT_DIFFERENCE: "💵",
    REFILL_NOT_RECONCILED: "🚚", MISSING_STOCK_UPDATE: "🚚", FUEL_PRICE_UPDATE_REQUIRED: "⛽", MONTHLY_REVIEW_REQUIRED: "📊",
  };
  App.alertIcon = (t) => ALERT_ICONS[t] || "⚠️";

  const plTable = (s) => `
    <table class="pl-table">
      <tr><td>Revenue</td><td>${App.money(s.total_revenue ?? s.revenue)}</td></tr>
      <tr><td>Fuel sold</td><td>${App.litres(s.fuel_sold_litres)}</td></tr>
      <tr><td>Fuel purchased / refilled</td><td>${App.litres(s.fuel_purchased_litres)}</td></tr>
      <tr><td>Fuel cost (cost of fuel sold)</td><td>${App.money(s.cost_of_fuel_sold)}</td></tr>
      <tr><td>Gross profit</td><td>${App.money(s.gross_profit)}</td></tr>
      <tr><td>Operating expenses</td><td>${App.money(s.operating_expenses)}</td></tr>
      <tr class="total ${Number(s.net_profit) < 0 ? "text-danger" : "text-ok"}">
        <td>${Number(s.net_profit) < 0 ? "Net Loss" : "Net Profit"}</td><td>${App.money(Math.abs(Number(s.net_profit)))}</td></tr>
    </table>`;

  function renderBanners(d) {
    const box = App.$("#banners");
    box.innerHTML = "";
    const st = d.period;
    if (st.monthly_review_required && st.previous_period) {
      const p = st.previous_period;
      const el = App.h(`
        <div class="banner banner--danger">
          <div class="banner__icon">📊</div>
          <div class="banner__body">
            <h2>PREVIOUS REPORTING PERIOD COMPLETE — MONTHLY FINANCIAL REVIEW</h2>
            <p>Your previous reporting period (${App.esc(p.label)}: ${App.date(p.start_date)} – ${App.date(p.end_date)}) has ended.
               Please review the station's revenue, expenses, fuel costs, and profit/loss.</p>
            ${plTable(p)}
            <div class="row">
              <a class="btn btn--primary" href="/manager/reports.html?type=monthly&start=${p.start_date}">VIEW FULL REPORT</a>
              <button class="btn" data-review="${p.id}">Mark as reviewed</button>
            </div>
          </div>
        </div>`);
      App.$("[data-review]", el).addEventListener("click", async () => {
        await App.post(`/api/periods/${p.id}/review`);
        App.toast("Monthly results marked as reviewed.", "ok");
        load();
      });
      box.appendChild(el);
    }
    if (st.price_review_required) {
      const el = App.h(`
        <div class="banner banner--warn"><div class="banner__icon">⛽</div><div class="banner__body">
          <h2>FUEL PRICE UPDATE REQUIRED</h2>
          <p>${st.is_first_wednesday ? "Today is the first Wednesday of the new reporting period." : "A new reporting period has started."}
             Please review and update the current fuel selling prices before continuing.</p>
          <div class="row"><button class="btn btn--warn">Review prices now</button></div></div></div>`);
      App.$("button", el).addEventListener("click", () => App.showPriceReview(st, load));
      box.appendChild(el);
    } else if (st.is_first_wednesday) {
      box.appendChild(App.h(`<div class="banner banner--info"><div class="banner__icon">📅</div><div class="banner__body">
        <h2>First Wednesday — new reporting period</h2><p>The ${App.esc(st.current_period.label)} reporting period started today.</p></div></div>`));
    }
  }

  function renderSetup(d) {
    const pricesSet = d.period.current_prices.every((p) => p.price_per_litre);
    const stockSet = d.stock.some((s) => Number(s.litres) > 0) || d.stock.every((s) => s.source !== "Opening balance");
    const staffOnShift = d.current_shift.staff.length > 0;
    const items = [
      [pricesSet, "Set the selling price for every fuel", "/manager/prices.html"],
      [stockSet, "Record the opening tank stock (and its cost) for each fuel", "/manager/stock.html"],
      [staffOnShift, "Assign staff to the current shift", "/manager/shifts.html"],
    ];
    if (items.every(([done]) => done)) { App.$("#setup").innerHTML = ""; return; }
    App.$("#setup").innerHTML = `
      <div class="card" style="margin-bottom:1rem"><div class="card__head"><h2>Getting started</h2></div>
        <ul class="checklist">${items.map(([done, text, href]) =>
          `<li class="${done ? "done" : ""}"><span class="tick">${done ? "✓" : ""}</span><a href="${href}">${text}</a></li>`).join("")}
        </ul></div>`;
  }

  function renderKpis(d) {
    const t = d.today_summary;
    const fuelTiles = t.by_fuel.filter((f) => Number(f.sold_qty) > 0 || d.stock.some((s) => s.fuel_type_id === f.fuel_type_id))
      .map((f) => `<div class="kpi"><div class="kpi__label">${App.fuelDot(f.color)}${App.esc(f.fuel)} sold</div>
        <div class="kpi__value">${App.litres(f.sold_qty)}</div><div class="kpi__sub">${App.money(f.sales_revenue)}</div></div>`).join("");
    const totalStock = d.stock.reduce((a, s) => a + Number(s.litres), 0);
    const profit = Number(t.net_profit);
    App.$("#today-kpis").innerHTML = `
      ${fuelTiles}
      <div class="kpi"><div class="kpi__label">Total fuel sold</div><div class="kpi__value">${App.litres(t.fuel_sold_litres)}</div></div>
      <div class="kpi"><div class="kpi__label">Revenue</div><div class="kpi__value">${App.money(t.revenue)}</div></div>
      <div class="kpi"><div class="kpi__label">Online money today</div><div class="kpi__value">${App.money(d.money_today.online_total)}</div>
        <div class="kpi__sub">${d.money_today.shifts.map((s) => `${App.esc(s.name)} ${App.money(s.online)}`).join(" + ") || "no shifts yet"}</div></div>
      <div class="kpi"><div class="kpi__label">Expenses</div><div class="kpi__value">${App.money(t.operating_expenses)}</div></div>
      <div class="kpi"><div class="kpi__label">Fuel cost</div><div class="kpi__value">${App.money(t.cost_of_fuel_sold)}</div><div class="kpi__sub">weighted-average cost of litres sold</div></div>
      <div class="kpi ${profit < 0 ? "kpi--loss" : "kpi--profit"}"><div class="kpi__label">${profit < 0 ? "Loss" : "Profit"}</div><div class="kpi__value">${App.money(Math.abs(profit))}</div></div>
      <div class="kpi"><div class="kpi__label">Current stock</div><div class="kpi__value">${App.litres(totalStock)}</div><div class="kpi__sub">all fuels</div></div>`;
    App.$("#today-label").textContent = `${App.date(d.today)} · Current shift: ${d.current_shift.label} (${App.label(d.current_shift.status)})`;
    App.$("#current-shift-link").href = `/manager/shifts.html?shift=${d.current_shift.id}`;
  }

  function renderWarnings(d) {
    const box = App.$("#warnings");
    if (!d.alerts.length) { box.innerHTML = '<div class="empty">✅ No open warnings.</div>'; return; }
    box.innerHTML = d.alerts.slice(0, 8).map((a) => `
      <div class="alert-item alert-item--${a.severity}">
        <div class="alert-item__icon">${App.alertIcon(a.alert_type)}</div>
        <div class="alert-item__body"><div class="alert-item__title">${App.esc(a.title)}</div>
          <div class="alert-item__msg">${App.esc(a.message)}</div>
          <div class="alert-item__meta">${App.label(a.alert_type)} · ${App.datetime(a.created_at)}</div></div>
      </div>`).join("") + (d.alert_count > 8 ? `<p class="muted small">+ ${d.alert_count - 8} more</p>` : "");
  }

  function renderPending(d) {
    const p = d.pending;
    const tile = (n, label, href) => `<a class="kpi" href="${href}"><div class="kpi__label">${label}</div><div class="kpi__value ${n ? "text-warn" : ""}">${n}</div></a>`;
    App.$("#pending").innerHTML =
      tile(p.shifts_to_review, "Shifts to review", "/manager/shifts.html?status=SUBMITTED") +
      tile(p.refills_to_approve, "Refills to approve", "/manager/refills.html?status=CONFIRMED") +
      tile(p.expenses_to_approve, "Expenses to approve", "/manager/expenses.html?status=PENDING");
    const cp = d.period.current_period;
    App.$("#period-label").textContent = `${cp.label}: ${App.date(cp.start_date)} – ${App.date(cp.end_date)}`;
    App.$("#period-pl").innerHTML = plTable(d.period_to_date) + '<p class="small muted">Period to date. Periods start on the first Wednesday of each month.</p>';
  }

  let moneyDatePicked = false;
  function renderMoney(d) {
    if (moneyDatePicked) return;            // the manager is looking at another day
    App.$("#money-date").value = d.money_today.date;
    App.$("#money-today").innerHTML = App.dayMoneyHtml(d.money_today);
  }
  App.$("#money-date").addEventListener("change", (e) => App.run(async () => {
    moneyDatePicked = true;
    App.$("#money-today").innerHTML = App.dayMoneyHtml(await App.get(`/api/money/day?date=${e.target.value}`));
  }));

  function renderStock(d) {
    App.$("#stock").innerHTML = d.tanks.map((t) => {
      const pct = Math.max(0, Math.min(100, t.percent_full));
      return `<a class="gauge ${t.is_low ? "gauge--low" : ""}" href="/manager/stock.html" style="color:inherit;text-decoration:none">
        <div class="gauge__head"><strong>${App.fuelDot(t.color)}${App.esc(t.name)}</strong>${t.is_low ? App.badge("CRITICAL", "LOW") : ""}</div>
        <div class="gauge__value">${App.litres(t.litres)}</div>
        <div class="gauge__bar"><div class="gauge__fill" style="width:${pct}%;background:${t.is_low ? "#dc2626" : App.esc(t.color)}"></div></div>
        <div class="small muted">${App.num(pct, 0)}% of ${App.litres(t.capacity_litres)} · Pumps: ${App.esc(t.pumps.join(", ") || "none")}</div>
        ${Number(t.litres) < 0 ? '<div class="small text-danger"><strong>Record this tank’s opening balance</strong></div>' : ""}
      </a>`;
    }).join("") || '<div class="empty">No tanks configured.</div>';
  }

  /* ---------------------------------------------------------------- charts */

  const charts = {};
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function barChart(id, labels, values, { money = true, diverging = false } = {}) {
    const brand = css("--brand") || "#0f766e";
    const colors = diverging ? values.map((v) => (Number(v) < 0 ? "#dc2626" : brand)) : brand;
    const fmt = (v) => (money ? App.money(v) : App.litres(v));
    const cfg = {
      type: "bar",
      data: { labels, datasets: [{ data: values.map(Number), backgroundColor: colors, borderRadius: 4, borderSkipped: "start", maxBarThickness: 28 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => fmt(c.parsed.y) } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: css("--muted"), maxRotation: 0, autoSkip: true } },
          y: { grid: { color: css("--border") }, border: { display: false },
               ticks: { color: css("--muted"), callback: (v) => new Intl.NumberFormat("en-US", { notation: "compact" }).format(v) } },
        },
      },
    };
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(document.getElementById(id), cfg);
  }

  async function loadCharts(granularity) {
    if (typeof Chart === "undefined") {
      App.$(".chart-grid").innerHTML = '<div class="empty">Charts need an internet connection to load the chart library. The data table below still works.</div>';
    }
    const s = await App.get(`/api/dashboard/charts?granularity=${granularity}`);
    if (typeof Chart !== "undefined") {
      barChart("chart-revenue", s.labels, s.revenue);
      barChart("chart-fuel", s.labels, s.fuel_sold, { money: false });
      barChart("chart-expenses", s.labels, s.expenses);
      barChart("chart-profit", s.labels, s.profit, { diverging: true });
    }
    App.$("#chart-table").innerHTML = App.table({
      columns: [
        { key: "label", label: "Period" },
        { key: "revenue", label: "Revenue", cls: "num", render: (r) => App.money(r.revenue) },
        { key: "fuel", label: "Fuel sold", cls: "num", render: (r) => App.litres(r.fuel) },
        { key: "cogs", label: "Fuel cost", cls: "num", render: (r) => App.money(r.cogs) },
        { key: "expenses", label: "Expenses", cls: "num", render: (r) => App.money(r.expenses) },
        { key: "profit", label: "Profit/loss", cls: "num", render: (r) => `<span class="${r.profit < 0 ? "text-danger" : ""}">${App.money(r.profit)}</span>` },
      ],
      rows: s.labels.map((label, i) => ({ label, revenue: s.revenue[i], fuel: s.fuel_sold[i], cogs: s.cost_of_fuel_sold[i], expenses: s.expenses[i], profit: s.profit[i] })),
    });
  }

  App.on(App.$("#chart-tabs"), "click", ".tab", (e, tab) => {
    App.$$("#chart-tabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
    App.run(() => loadCharts(tab.dataset.g));
  });

  async function load() {
    const d = await App.get("/api/dashboard/manager");
    renderBanners(d);
    renderSetup(d);
    renderKpis(d);
    renderWarnings(d);
    renderPending(d);
    renderStock(d);
    renderMoney(d);
    App.refreshAlertCount();
  }

  await Promise.all([load(), loadCharts("daily")]);
  setInterval(() => App.run(load), 60 * 1000); // live refresh every minute
});
