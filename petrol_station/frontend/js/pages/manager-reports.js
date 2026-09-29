/* REPORTS — preview any report, export PDF / Excel, reporting periods */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Reports" });
  const page = App.$("#page");
  const params = new URLSearchParams(location.search);
  const { types } = await App.get("/api/reports/types");
  const NEEDS_RANGE = ["fuel_sales", "fuel_stock", "refills", "expenses", "profit_loss", "staff_shifts", "audit", "daily_money"];

  page.innerHTML = `
    <div class="page-head"><div><h1>Reports</h1><p>Monthly reports follow the first-Wednesday reporting period.</p></div></div>
    <div class="card">
      <form id="report-form" class="filters">
        <div class="field"><label>Report</label><select class="input" name="type">${App.options(types, "key", "label", params.get("type") || "daily")}</select></div>
        <div class="field" data-f="start"><label id="start-label">Date</label><input class="input" type="date" name="start" value="${params.get("start") || App.serverToday()}"></div>
        <div class="field" data-f="end"><label>To</label><input class="input" type="date" name="end" value="${App.serverToday()}"></div>
        <div class="field hidden" data-f="shift"><label>Shift</label><select class="input" name="shift_id"></select></div>
        <button class="btn btn--primary" type="submit">Preview</button>
        <button class="btn" type="button" data-export="pdf">⬇ PDF</button>
        <button class="btn" type="button" data-export="xlsx">⬇ Excel</button>
      </form>
    </div>
    <div id="report"></div>
    <div class="card"><h2>Reporting periods</h2><div id="periods"></div></div>
    <div class="card"><h2>Export history</h2><div id="history"></div></div>`;

  const form = App.$("#report-form");
  const typeSel = App.$('[name="type"]', form);

  async function syncFields() {
    const t = typeSel.value;
    App.$('[data-f="shift"]', form).classList.toggle("hidden", t !== "shift");
    App.$('[data-f="start"]', form).classList.toggle("hidden", t === "shift");
    App.$('[data-f="end"]', form).classList.toggle("hidden", !NEEDS_RANGE.includes(t));
    App.$("#start-label").textContent = NEEDS_RANGE.includes(t) ? "From" : "Any date in the period";
    if (t === "shift" && !App.$('[name="shift_id"]', form).options.length) {
      const { shifts } = await App.get("/api/shifts");
      App.$('[name="shift_id"]', form).innerHTML = App.options(shifts, "id", "label");
    }
  }

  const query = (format) => {
    const d = App.formData(form);
    const t = d.type;
    return `/api/reports/${t}` + App.qs({
      start: t === "shift" ? "" : d.start, end: NEEDS_RANGE.includes(t) ? d.end : "",
      shift_id: t === "shift" ? d.shift_id : "", format,
    });
  };

  const fmtCell = (v, kind) => kind === "money" ? App.money(v, false) : kind === "litres" ? App.litres(v) : kind === "date" ? App.date(v)
    : kind === "number" ? App.num(v) : App.esc(v ?? "—");

  async function preview() {
    const { report } = await App.get(query("json"));
    App.$("#report").innerHTML = `<div class="card">
      <div class="card__head"><div><h2>${App.esc(report.title)}</h2><p class="muted">${App.esc(report.subtitle || "")}</p></div></div>
      ${report.summary.length ? `<table class="pl-table">${report.summary.map(([l, v, k]) =>
        `<tr class="${/^Net (profit|loss)/i.test(l) ? "total " + (/loss/i.test(l) ? "text-danger" : "text-ok") : ""}"><td>${App.esc(l)}</td><td>${k === "money" ? App.money(v) : fmtCell(v, k)}</td></tr>`).join("")}</table>` : ""}
      ${report.tables.map((t) => `<h3 style="margin-top:1rem">${App.esc(t.title)}</h3>` + App.table({
        columns: t.columns.map(([key, label, kind]) => ({ key, label, cls: ["money", "litres", "number"].includes(kind) ? "num" : "", render: (r) => fmtCell(r[key], kind) })),
        rows: t.rows, empty: "No records for this period.", cards: t.columns.length <= 8 })).join("")}
    </div>`;
  }

  async function loadSide() {
    const [{ periods }, { reports }] = await Promise.all([App.get("/api/periods"), App.get("/api/reports/history")]);
    App.$("#periods").innerHTML = App.table({
      columns: [
        { key: "label", label: "Period", render: (p) => `<strong>${App.esc(p.label)}</strong><div class="small muted">${App.date(p.start_date)} – ${App.date(p.end_date)}</div>` },
        { key: "status", label: "Status", render: (p) => App.badge(p.status === "OPEN" ? "OPEN" : "CLOSED", p.status === "OPEN" ? "Current" : "Closed") + (p.reviewed_at ? " " + App.badge("ok", "Reviewed") : "") },
        { key: "total_revenue", label: "Revenue", cls: "num", render: (p) => App.money(p.total_revenue) },
        { key: "cost_of_fuel_sold", label: "Fuel cost", cls: "num", render: (p) => App.money(p.cost_of_fuel_sold) },
        { key: "operating_expenses", label: "Expenses", cls: "num", render: (p) => App.money(p.operating_expenses) },
        { key: "net_profit", label: "Net profit / loss", cls: "num", render: (p) => p.net_profit == null ? "—" : `<span class="${Number(p.net_profit) < 0 ? "text-danger" : "text-ok"}">${App.money(p.net_profit)}</span>` },
        { key: "a", label: "", cls: "actions", render: (p) => `<button class="btn btn--sm" data-period="${p.start_date}">Open report</button>` },
      ], rows: periods,
    });
    App.$("#history").innerHTML = App.table({
      columns: [
        { key: "created_at", label: "When", render: (r) => App.datetime(r.created_at) },
        { key: "label", label: "Report" },
        { key: "period", label: "Period", render: (r) => `${App.date(r.period_start)} – ${App.date(r.period_end)}` },
        { key: "format", label: "Format", render: (r) => App.esc(r.format.toUpperCase()) },
        { key: "generated_by_name", label: "By" },
      ], rows: reports, empty: "No exports yet.",
    });
  }

  typeSel.addEventListener("change", () => App.run(syncFields));
  form.addEventListener("submit", (e) => { e.preventDefault(); App.run(preview); });
  App.on(form, "click", "[data-export]", (e, b) => { App.download(query(b.dataset.export)); setTimeout(() => App.run(loadSide), 1500); });
  App.on(page, "click", "[data-period]", (e, b) => {
    typeSel.value = "monthly";
    App.$('[name="start"]', form).value = b.dataset.period;
    App.run(async () => { await syncFields(); await preview(); window.scrollTo({ top: 0, behavior: "smooth" }); });
  });

  await syncFields();
  await Promise.all([preview(), loadSide()]);
});
