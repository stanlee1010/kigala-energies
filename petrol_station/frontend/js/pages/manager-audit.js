/* AUDIT LOG — who did what, when, previous value, new value, why */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Audit Log" });
  const page = App.$("#page");
  const { users } = await App.get("/api/users");
  const TABLES = ["", "fuel_refills", "pump_readings", "fuel_stock", "shifts", "shift_deposits", "fuel_prices", "expenses", "users", "pumps", "stock_adjustments", "alerts", "system_settings"];

  page.innerHTML = `
    <div class="page-head"><div><h1>Audit log</h1><p>Every important change is recorded permanently.</p></div>
      <a class="btn" href="/manager/reports.html?type=audit">Export audit report</a></div>
    <div class="card">
      <form id="filters" class="filters">
        <div class="field"><label>User</label><select class="input" name="user_id">${App.options(users, "id", "full_name", "", "Everyone")}</select></div>
        <div class="field"><label>Record type</label><select class="input" name="table">${App.options(TABLES.map((t) => ({ t, l: t ? App.label(t) : "All" })), "t", "l", "")}</select></div>
        <div class="field"><label>Action contains</label><input class="input" name="search" placeholder="e.g. CORRECTED"></div>
        <div class="field"><label>From</label><input class="input" type="date" name="start"></div>
        <div class="field"><label>To</label><input class="input" type="date" name="end"></div>
        <button class="btn btn--primary" type="submit">Search</button>
      </form>
      <div id="list"></div>
    </div>`;

  const show = (v) => v ? `<code>${App.esc(Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(", "))}</code>` : "—";

  async function load() {
    const { logs } = await App.get("/api/audit" + App.qs(App.formData(App.$("#filters"))));
    App.$("#list").innerHTML = App.table({
      columns: [
        { key: "created_at", label: "When", render: (l) => App.datetime(l.created_at) },
        { key: "user", label: "User" },
        { key: "action", label: "Action", render: (l) => `<strong>${App.esc(l.action)}</strong>` },
        { key: "record", label: "Record", render: (l) => App.esc(`${l.table_name ? App.label(l.table_name) : ""}${l.record_id ? " #" + l.record_id : ""}`) },
        { key: "old_values", label: "Previous value", render: (l) => show(l.old_values) },
        { key: "new_values", label: "New value", render: (l) => show(l.new_values) },
        { key: "reason", label: "Why", render: (l) => App.esc(l.reason || "—") },
        { key: "ip_address", label: "IP", render: (l) => App.esc(l.ip_address || "") },
      ], rows: logs, empty: "No matching entries.",
    });
  }
  App.$("#filters").addEventListener("submit", (e) => { e.preventDefault(); App.run(load); });
  await load();
});
