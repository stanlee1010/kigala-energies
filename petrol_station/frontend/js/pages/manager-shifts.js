/* SHIFT MANAGEMENT — generate daily shifts, assign staff, review, approve/flag, deposits */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Shift Management" });
  const page = App.$("#page");
  const params = new URLSearchParams(location.search);
  const today = App.serverToday();
  const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

  page.innerHTML = `
    <div class="page-head"><div><h1>Shift management</h1>
      <p>Shifts are created automatically every day from your shift pattern, and your regular staff are put on them.</p></div></div>
    <section class="card" id="pattern"></section>
    <section class="card" id="week-plan"></section>
    <section class="card" id="roster"></section>
    <div class="card">
      <h2>Create shifts for a date range</h2>
      <form id="gen-form" class="filters" novalidate>
        <div class="field"><label>From</label><input class="input" type="date" name="start_date" value="${today}" required></div>
        <div class="field"><label>To</label><input class="input" type="date" name="end_date" value="${addDays(today, 6)}" required></div>
        <button class="btn btn--primary" type="submit">Create shifts</button>
      </form>
      <div id="gen-result" class="small muted"></div>
    </div>
    <div class="card">
      <h2>Shifts</h2>
      <form id="filter-form" class="filters">
        <div class="field"><label>From</label><input class="input" type="date" name="start" value="${params.get("start") || addDays(today, -6)}"></div>
        <div class="field"><label>To</label><input class="input" type="date" name="end" value="${params.get("end") || addDays(today, 1)}"></div>
        <div class="field"><label>Status</label><select class="input" name="status">
          ${App.options(["", "SCHEDULED", "OPEN", "SUBMITTED", "FLAGGED", "CLOSED", "CANCELLED"].map((s) => ({ v: s, l: s ? App.label(s) : "All" })), "v", "l", params.get("status") || "")}</select></div>
        <button class="btn" type="submit">Show</button>
      </form>
      <div id="list"></div>
    </div>`;

  let staff = [], pumps = [];
  [{ users: staff }, { pumps }] = await Promise.all([App.get("/api/users"), App.get("/api/pumps")]);

  /* ------------------------------------------------ shift pattern (types) */

  function renderPattern(data) {
    const active = data.shift_types.filter((t) => t.is_active);
    const perShift = active.length ? (data.active_staff / active.length) : 0;
    App.$("#pattern").innerHTML = `
      <div class="card__head"><h2>Shift pattern</h2><button class="btn btn--sm btn--primary" id="add-type">+ Add shift</button></div>
      <p class="small muted">${active.length} shift(s) a day · ${data.active_staff} active staff
        (about ${App.num(perShift, 1)} per shift). Each shift ends when the next one starts.</p>
      ${App.table({
        columns: [
          { key: "name", label: "Shift", render: (t) => `<strong>${App.esc(t.name)}</strong>` },
          { key: "start_time", label: "Hours", cls: "nowrap", render: (t) => t.is_active ? `${t.start_time} – ${t.end_time}${t.ends_next_day ? " (+1 day)" : ""} · ${App.num(t.hours, 1)} h` : App.esc(t.start_time) },
          { key: "staff_count", label: "Regular staff", cls: "num", render: (t) => t.is_active && !t.staff_count ? '<span class="text-warn">0</span>' : t.staff_count },
          { key: "is_active", label: "Status", render: (t) => t.is_active ? App.badge("ACTIVE", "On") : App.badge("DISABLED", "Off") },
          { key: "a", label: "", cls: "actions", render: (t) => `<button class="btn btn--sm" data-edit-type="${t.id}">Edit</button>
            <button class="btn btn--sm ${t.is_active ? "btn--danger" : "btn--ok"}" data-toggle-type="${t.id}">${t.is_active ? "Switch off" : "Switch on"}</button>` },
        ], rows: data.shift_types })}
      <div class="row" style="margin-top:.75rem">
        <span class="small muted">Quick patterns:</span>
        <button class="btn btn--sm" data-preset="TWO">2 shifts × 12 h (Morning, Night)</button>
        <button class="btn btn--sm" data-preset="THREE">3 shifts × 8 h (Morning, Afternoon, Night)</button>
      </div>`;
  }

  function typeForm(t) {
    return App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Shift name</label><input class="input" name="name" value="${App.esc(t ? t.name : "")}" placeholder="e.g. Afternoon" required></div>
      <div class="field"><label>Starts at</label><input class="input" type="time" name="start_time" value="${t ? t.start_time : "14:00"}" required></div>
    </div><p class="small muted">The shift runs until the next shift starts. Shifts that have not started yet are re-timed automatically.</p></form>`);
  }

  async function patternChanged(res, message) {
    renderPattern(res);
    App.toast(message + (res.cancelled_shifts ? ` ${res.cancelled_shifts} upcoming shift(s) of removed types were cancelled.` : ""), "ok", 7000);
    await Promise.all([loadRoster(), loadList()]);
  }

  App.on(page, "click", "#add-type", () => {
    const form = typeForm(null);
    App.modal({ title: "Add a shift", body: form, actions: [{ label: "Cancel" }, {
      label: "Add shift", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        await patternChanged(await App.post("/api/shift-types", App.formData(form)), "Shift added.");
      },
    }] });
  });
  App.on(page, "click", "[data-edit-type]", (e, b) => App.run(async () => {
    const t = (await App.get("/api/shift-types")).shift_types.find((x) => String(x.id) === b.dataset.editType);
    const form = typeForm(t);
    App.modal({ title: `Edit ${App.esc(t.name)} shift`, body: form, actions: [{ label: "Cancel" }, {
      label: "Save", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        await patternChanged(await App.put(`/api/shift-types/${t.id}`, App.formData(form)), "Shift updated.");
      },
    }] });
  }));
  App.on(page, "click", "[data-toggle-type]", (e, b) => App.run(async () => {
    const t = (await App.get("/api/shift-types")).shift_types.find((x) => String(x.id) === b.dataset.toggleType);
    if (t.is_active && !(await App.ask({ title: `Switch off ${App.esc(t.name)}?`, danger: true, confirmLabel: "Switch off",
      message: "Upcoming shifts of this type that have not started will be cancelled (never deleted). Shifts already worked are kept." }))) return;
    await patternChanged(await App.put(`/api/shift-types/${t.id}`, { is_active: !t.is_active }), `${t.name} switched ${t.is_active ? "off" : "on"}.`);
  }));
  App.on(page, "click", "[data-preset]", (e, b) => App.run(async () => {
    const three = b.dataset.preset === "THREE";
    if (!(await App.ask({ title: three ? "Use 3 shifts a day?" : "Use 2 shifts a day?", confirmLabel: "Apply",
      message: three ? "Morning 06:00–14:00, Afternoon 14:00–22:00, Night 22:00–06:00. Upcoming shifts that have not started are re-timed; staff keep their regular shift where it still exists."
                     : "Morning 06:00–18:00 and Night 18:00–06:00. Upcoming Afternoon shifts that have not started are cancelled." }))) return;
    await patternChanged(await App.post("/api/shift-types/preset", { preset: b.dataset.preset }), "Shift pattern applied.");
  }));

  /* ------------------------------------------ weekly choices (from Monday) */

  let planWeek = "";
  async function loadWeekPlan() {
    const d = await App.get("/api/roster/week-choices" + App.qs({ week: planWeek }));
    planWeek = d.week_start;
    const choices = [{ id: "", name: "— choose —" }, ...d.shift_types];
    App.$("#week-plan").innerHTML = `
      <div class="card__head"><h2>📅 Shifts for the week</h2>
        <div class="row"><button class="btn btn--sm" data-week-move="-7">◀</button>
          <strong>${App.date(d.week_start)} – ${App.date(d.week_end)}</strong>
          <button class="btn btn--sm" data-week-move="7">▶</button></div></div>
      <p class="small muted">Staff choose their shift for next week on their phone; the change starts on Monday.
        Anyone who has not chosen works their regular shift. You can also choose for them here.</p>
      <div class="kpis" style="margin-bottom:.75rem">${Object.entries(d.counts).map(([name, n]) =>
        `<div class="kpi"><div class="kpi__label">${App.esc(name)}</div><div class="kpi__value ${name === "Not set" && n ? "text-warn" : ""}">${n}</div></div>`).join("")}</div>
      ${App.table({
        columns: [
          { key: "full_name", label: "Staff", render: (u) => `<strong>${App.esc(u.full_name)}</strong>` },
          { key: "shift", label: "Shift that week", render: (u) => u.shift
              ? `${App.esc(u.shift)} ${u.chosen ? App.badge("ok", "chosen by staff") : '<span class="badge">regular shift</span>'}`
              : '<span class="text-warn">Not chosen yet</span>' },
          { key: "set", label: "Set for this week", render: (u) => `<select class="input input--sm" data-week-user="${u.id}">
              ${App.options(choices, "id", "name", "")}</select>` },
        ], rows: d.staff, empty: "No active staff at this station." })}`;
  }
  App.on(page, "click", "[data-week-move]", (e, b) => {
    const dt = new Date(planWeek + "T00:00:00Z");
    dt.setUTCDate(dt.getUTCDate() + Number(b.dataset.weekMove));
    planWeek = dt.toISOString().slice(0, 10);
    App.run(loadWeekPlan);
  });
  App.on(page, "change", "[data-week-user]", (e, sel) => App.run(async () => {
    if (!sel.value) return;
    const r = await App.put(`/api/roster/week-choices/${sel.dataset.weekUser}`, { week: planWeek, shift_type_id: Number(sel.value) });
    App.toast(`Set to ${r.shift} for the week of ${App.date(r.week_start)}.`, "ok");
    await Promise.all([loadWeekPlan(), loadList()]);
  }));

  /* --------------------------------------------------- regular roster */

  async function loadRoster() {
    const data = await App.get("/api/roster");
    renderPattern(data);
    const active = data.shift_types.filter((t) => t.is_active);
    const people = data.staff.filter((u) => u.is_active);
    const board = active.map((t) => {
      const names = people.filter((u) => u.shift_type_id === t.id).map((u) => App.esc(u.full_name));
      return `<div class="kpi"><div class="kpi__label">${App.esc(t.name)} · ${t.start_time}–${t.end_time}</div>
        <div class="small">${names.length ? names.join(", ") : '<span class="text-warn">Nobody yet</span>'}</div></div>`;
    }).join("");
    const choices = [{ id: "", name: "— Not set —" }, ...active];
    App.$("#roster").innerHTML = `
      <div class="card__head"><h2>Who works which shift</h2></div>
      <p class="small muted">Pick each person's regular shift. They are put on every upcoming shift of that type automatically.
        To change a single day, open that shift below.</p>
      <div class="kpis" style="margin-bottom:.75rem">${board}</div>
      ${App.table({
        columns: [
          { key: "full_name", label: "Staff", render: (u) => `<strong>${App.esc(u.full_name)}</strong> <span class="small muted">${App.esc(u.username)}</span>` },
          { key: "shift", label: "Regular shift", render: (u) => `<select class="input input--sm" data-roster="${u.id}">
              ${App.options(choices, "id", "name", u.shift_type_id ?? "")}</select>` },
        ], rows: people, empty: "No active staff yet. Create staff accounts on the Staff page." })}`;
  }

  App.on(page, "change", "[data-roster]", (e, sel) => App.run(async () => {
    sel.disabled = true;
    try {
      const res = await App.put(`/api/roster/${sel.dataset.roster}`, { shift_type_id: sel.value || null });
      const name = sel.options[sel.selectedIndex].text;
      App.toast(sel.value ? `Regular shift set to ${name}. Added to ${res.added_to} upcoming shift(s).`
                          : `Regular shift removed. Taken off ${res.removed_from} upcoming shift(s).`, "ok");
      await Promise.all([loadRoster(), loadList()]);
    } finally { sel.disabled = false; }
  }));

  /* --------------------------------------------------------------- list */

  async function loadList() {
    const f = App.formData(App.$("#filter-form"));
    const res = await App.get("/api/shifts" + App.qs(f));
    App.$("#list").innerHTML = App.table({
      columns: [
        { key: "label", label: "Shift", render: (s) => `<strong>${App.esc(s.label)}</strong>${s.id === res.current_shift_id ? " " + App.badge("OPEN", "NOW") : ""}` },
        { key: "status", label: "Status", render: (s) => App.badge(s.status) },
        { key: "staff", label: "Staff", render: (s) => s.staff.length ? App.esc(s.staff.join(", ")) : '<span class="text-warn">Not assigned</span>' },
        { key: "total_litres", label: "Fuel sold", cls: "num", render: (s) => App.litres(s.total_litres) },
        { key: "expected_money", label: "Expected money", cls: "num", render: (s) => App.money(s.expected_money) },
        { key: "deposited", label: "Deposited", cls: "num", render: (s) => App.money(s.deposited) },
        { key: "deposit_difference", label: "Difference", cls: "num", render: (s) => s.deposit_difference == null ? "—" : `<span class="${Number(s.deposit_difference) ? "text-danger" : "text-ok"}">${App.signed(s.deposit_difference, App.money)}</span>` },
        { key: "a", label: "", cls: "actions", render: (s) => `<button class="btn btn--sm btn--primary" data-open="${s.id}">Open</button>` },
      ],
      rows: res.shifts, empty: "No shifts in this range.",
    });
  }

  App.$("#filter-form").addEventListener("submit", (e) => { e.preventDefault(); App.run(loadList); });
  App.$("#gen-form").addEventListener("submit", (e) => {
    e.preventDefault();
    App.run(async () => {
      const res = await App.post("/api/shifts/generate", App.formData(e.target));
      App.$("#gen-result").textContent = `${res.created} new shift(s) created, ${res.total - res.created} already existed (never duplicated). ` +
        res.shifts.map((s) => s.label).join(" · ");
      App.toast(`${res.created} shift(s) created.`, "ok");
      loadList();
    });
  });
  App.on(page, "click", "[data-open]", (e, b) => App.run(() => openShift(b.dataset.open)));

  /* ------------------------------------------------------------- detail */

  async function openShift(id) {
    const { shift: s } = await App.get(`/api/shifts/${id}`);
    const locked = s.status === "CLOSED";
    const activeStaff = staff.filter((u) => u.is_active && u.role === "STAFF");
    const body = App.h(`<div class="stack">
      <div class="row">${App.badge(s.status)}
        <span class="muted">${App.localDateTime(s.scheduled_start)} – ${App.localDateTime(s.scheduled_end)}</span>
        ${s.previous_shift ? `<span class="muted">· Previous: ${App.esc(s.previous_shift.label)} (${App.label(s.previous_shift.status)})</span>` : ""}
        <span class="spacer"></span>
        <a class="btn btn--sm" href="/api/reports/shift?shift_id=${s.id}&format=pdf">PDF</a>
        <a class="btn btn--sm" href="/api/reports/shift?shift_id=${s.id}&format=xlsx">Excel</a>
      </div>
      ${s.flag_reason && s.status === "FLAGGED" ? `<div class="banner banner--danger"><div class="banner__icon">🚩</div><div class="banner__body"><h2>Flagged</h2><p>${App.esc(s.flag_reason)}</p></div></div>` : ""}

      <div class="kpis">
        <div class="kpi"><div class="kpi__label">Money expected from this shift</div><div class="kpi__value">${App.money(s.expected_money)}</div>
          <div class="kpi__sub">${s.revenue_by_fuel.map((f) => `${App.esc(f.fuel)} ${App.money(f.revenue)}`).join(" + ") || "No sales yet"}</div></div>
        <div class="kpi"><div class="kpi__label">Fuel sold</div><div class="kpi__value">${App.litres(s.total_litres)}</div></div>
        <div class="kpi"><div class="kpi__label">Deposited</div><div class="kpi__value">${App.money(s.deposit ? s.deposit.actual_amount : null)}</div>
          ${s.deposit ? `<div class="kpi__sub ${Number(s.deposit.difference) ? "text-danger" : "text-ok"}">Difference ${App.signed(s.deposit.difference, App.money)}</div>` : ""}</div>
      </div>

      <section><h3>Assigned staff</h3>
        ${App.table({ columns: [
          { key: "full_name", label: "Staff", render: (a) => App.esc(a.full_name) + (a.source === "ROSTER" ? ' <span class="badge">regular</span>' : a.source === "SELF" ? ' <span class="badge">started it</span>' : "") },
          { key: "pumps", label: "Pumps", render: (a) => App.esc(a.pumps.join(", ") || "All pumps") },
          { key: "x", label: "", cls: "actions", render: (a) => locked ? "" : `<button class="btn btn--sm" data-unassign="${a.user_id}">Remove</button>` },
        ], rows: s.staff, empty: "Nobody assigned yet." })}
        ${locked ? "" : `<form id="assign-form" class="stack" style="margin-top:.75rem">
          <div class="filters">
            <div class="field"><label>Staff member</label><select class="input" name="user_id" required>${App.options(activeStaff, "id", "full_name", null, "Choose staff…")}</select></div>
            <button class="btn btn--primary" type="submit">Assign / update</button>
          </div>
          <div class="row small">Pumps (leave all unticked = all pumps):
            ${pumps.filter((p) => p.is_active).map((p) => `<label class="checkbox"><input type="checkbox" name="pump" value="${p.id}"> ${App.esc(p.pump_name)}</label>`).join("")}</div>
        </form>`}
      </section>

      <section><h3>Pump readings</h3>
        ${App.table({ columns: [
          { key: "pump_name", label: "Pump", render: (r) => App.fuelDot(r.fuel_color) + App.esc(r.pump_name) },
          { key: "opening_meter", label: "Opening", cls: "num", render: (r) => App.num(r.opening_meter, 3) + (r.expected_opening_meter != null && Number(r.expected_opening_meter) !== Number(r.opening_meter) ? ` <span class="text-danger" title="Previous closing ${r.expected_opening_meter}">⚠️</span>` : "") },
          { key: "closing_meter", label: "Closing", cls: "num", render: (r) => App.num(r.closing_meter, 3) },
          { key: "fuel_sold", label: "Sold", cls: "num", render: (r) => App.litres(r.fuel_sold) },
          { key: "price_per_litre", label: "Price", cls: "num", render: (r) => App.money(r.price_per_litre, false) },
          { key: "revenue", label: "Revenue", cls: "num", render: (r) => App.money(r.revenue) },
          { key: "x", label: "", cls: "actions", render: (r) => locked ? "" : `<button class="btn btn--sm" data-correct="${r.pump_id}">Correct</button>` },
        ], rows: s.readings, empty: "No readings yet — the shift has not been started." })}
      </section>

      <section><h3>Tanks in this shift</h3>
        ${s.tanks.length ? `<div class="grid-2">${s.tanks.map(App.tankCard).join("")}</div>` : '<div class="empty">No tank movement yet.</div>'}
      </section>

      <section><h3>Fuel stock by fuel (Closing = Opening + Refilled − Sold)</h3>
        ${App.table({ columns: [
          { key: "fuel", label: "Fuel", render: (r) => App.fuelDot(r.fuel_color) + App.esc(r.fuel) },
          { key: "expected_opening_stock", label: "Prev. closing", cls: "num", render: (r) => App.litres(r.expected_opening_stock) },
          { key: "opening_stock", label: "Opening", cls: "num", render: (r) => App.litres(r.opening_stock) + (Number(r.opening_difference) ? ` <span class="text-danger">(${App.signed(r.opening_difference, App.num)})</span>` : "") },
          { key: "refilled_quantity", label: "+ Refilled", cls: "num", render: (r) => App.litres(r.refilled_quantity) },
          { key: "fuel_sold", label: "− Sold", cls: "num", render: (r) => App.litres(r.fuel_sold) },
          { key: "calculated_closing_stock", label: "= Closing", cls: "num", render: (r) => `<strong>${App.litres(r.calculated_closing_stock)}</strong>` },
          { key: "actual_closing_stock", label: "Dip", cls: "num", render: (r) => App.litres(r.actual_closing_stock) },
          { key: "status", label: "Status", render: (r) => App.badge(r.status) + (locked ? ` <button class="btn btn--sm" data-reconcile="${r.id}">Reconcile</button>` : "") },
        ], rows: s.stock, empty: "No stock records." })}
      </section>

      <section><h3>Refills in this shift</h3>
        ${App.table({ columns: [
          { key: "fuel", label: "Fuel" },
          { key: "refill_time", label: "Time", render: (r) => App.esc(String(r.refill_time).slice(0, 5)) },
          { key: "quantity_litres", label: "Quantity", cls: "num", render: (r) => App.litres(r.quantity_litres) },
          { key: "total_cost", label: "Cost", cls: "num", render: (r) => App.money(r.total_cost) },
          { key: "recorded_by_name", label: "Recorded by" },
          { key: "status", label: "Status", render: (r) => App.badge(r.status) },
        ], rows: s.refills, empty: "No refills." })}
      </section>

      <section class="card"><h3>Shift money deposit</h3>
        <form id="deposit-form" novalidate><div class="form-grid">
          <div class="field span-all"><table class="pl-table">
            <tr><td>Total (fuel sales)</td><td>${App.money(s.money.total)}</td></tr>
            <tr><td>− Online money <span class="small muted">${App.esc(s.money.online_expression || "")}</span></td><td>${App.money(s.money.online)}</td></tr>
            <tr><td>= Total cash</td><td>${App.money(s.money.total_cash)}</td></tr>
            <tr><td>− Expenses paid from the cash</td><td>${App.money(s.money.expenses)}</td></tr>
            <tr class="total"><td>= Cash expected to be deposited</td><td>${App.money(s.money.balance)}</td></tr>
          </table></div>
          ${["CLOSED", "CANCELLED"].includes(s.status) ? "" : `<div class="field span-all"><label>Online money for this shift (use + to add)</label>
            <div class="row"><input class="input" style="flex:1" id="mgr-online" value="${App.esc(s.money.online_expression || "")}" placeholder="4000+60000+5000">
            <button class="btn" type="button" id="mgr-online-save">Save online money</button></div></div>`}
          <div class="field"><label>Actual amount deposited</label><input class="input" type="number" min="0" step="0.01" name="actual_amount" value="${s.deposit ? s.deposit.actual_amount : ""}" required></div>
          <div class="field"><label>Deposit reference</label><input class="input" name="deposit_reference" value="${App.esc(s.deposit ? s.deposit.deposit_reference || "" : "")}"></div>
          <div class="field"><label>Method</label><select class="input" name="deposit_method">${App.options(["Cash", "Bank deposit", "Mobile money", "Card", "Other"].map((m) => ({ m })), "m", "m", s.deposit ? s.deposit.deposit_method : "Bank deposit")}</select></div>
          <div class="field"><label>Account</label><input class="input" name="deposit_account" value="${App.esc(s.deposit ? s.deposit.deposit_account || "" : "")}"></div>
          <div class="field"><label>Date / time</label><input class="input" type="datetime-local" name="deposited_at" value="${s.deposit ? String(s.deposit.deposited_at).slice(0, 16) : App.serverNow().toISOString().slice(0, 16)}"></div>
          <div class="field span-all"><label>Notes</label><input class="input" name="notes" value="${App.esc(s.deposit ? s.deposit.notes || "" : "")}"></div>
        </div><div class="form-actions"><button class="btn btn--primary" type="submit">Save deposit</button></div></form>
      </section>
    </div>`);

    const actions = [{ label: "Close window" }];
    if (s.status === "SCHEDULED") actions.push({ label: "Open shift", cls: "btn--primary", onClick: async () => { await App.post(`/api/shifts/${s.id}/start`); App.toast("Shift opened.", "ok"); reopen(); } });
    if (["OPEN", "SUBMITTED"].includes(s.status)) actions.push({ label: "🚩 Flag", cls: "btn--danger", onClick: async () => {
      const reason = await App.ask({ title: "Flag shift", message: "The staff member will see your reason and can correct the shift.", confirmLabel: "Flag shift", danger: true, input: { label: "Reason", required: true } });
      if (!reason) return false;
      await App.post(`/api/shifts/${s.id}/flag`, { reason }); App.toast("Shift flagged.", "ok"); reopen();
    } });
    if (["OPEN", "SUBMITTED", "FLAGGED"].includes(s.status)) actions.push({ label: "✔ Approve & close shift", cls: "btn--ok", onClick: async () => {
      if (!(await App.ask({ title: "Approve shift", message: `Close ${App.esc(s.label)}? Expected money: <strong>${App.money(s.expected_money)}</strong>.`, confirmLabel: "Approve & close" }))) return false;
      await App.post(`/api/shifts/${s.id}/approve`); App.toast("Shift approved and closed.", "ok"); reopen();
    } });
    if (s.status === "CLOSED") actions.push({ label: "Reopen for correction", onClick: async () => {
      const reason = await App.ask({ title: "Reopen shift", message: "Reopening allows corrections. The change is recorded in the audit log.", confirmLabel: "Reopen", input: { label: "Reason", required: true } });
      if (!reason) return false;
      await App.post(`/api/shifts/${s.id}/reopen`, { reason }); App.toast("Shift reopened.", "ok"); reopen();
    } });

    const modal = App.modal({ title: App.esc(s.label), body, wide: true, actions });
    function reopen() { modal.close(); loadList(); openShift(s.id); }

    const assign = App.$("#assign-form", body);
    if (assign) assign.addEventListener("submit", (e) => {
      e.preventDefault();
      App.run(async () => {
        const userId = App.$('[name="user_id"]', assign).value;
        if (!userId) { App.toast("Choose a staff member", "error"); return; }
        const pumpIds = App.$$('[name="pump"]:checked', assign).map((c) => Number(c.value));
        await App.post(`/api/shifts/${s.id}/assign`, { user_id: Number(userId), pump_ids: pumpIds });
        App.toast("Staff assigned.", "ok");
        reopen();
      });
    });
    App.on(body, "click", "[data-unassign]", (e, b) => App.run(async () => {
      await App.del(`/api/shifts/${s.id}/assign/${b.dataset.unassign}`);
      App.toast("Staff removed from shift.", "ok"); reopen();
    }));
    App.on(body, "click", "[data-reconcile]", (e, b) => App.run(async () => {
      const reason = await App.ask({ title: "Reconcile stock", message: "Recalculate this stock record from all confirmed refills and pump readings.", confirmLabel: "Reconcile", input: { label: "Reason", required: true } });
      if (!reason) return;
      await App.post(`/api/stock/${b.dataset.reconcile}/reconcile`, { reason });
      App.toast("Stock reconciled.", "ok"); reopen();
    }));
    App.on(body, "click", "[data-correct]", (e, b) => {
      const r = s.readings.find((x) => String(x.pump_id) === b.dataset.correct);
      const form = App.h(`<form novalidate><div class="form-grid">
        <div class="field"><label>Opening meter</label><input class="input" type="number" step="0.001" min="0" name="opening_meter" value="${r.opening_meter}"></div>
        <div class="field"><label>Closing meter</label><input class="input" type="number" step="0.001" min="0" name="closing_meter" value="${r.closing_meter ?? ""}"></div>
        <div class="field span-all"><label>Reason for correction</label><textarea class="input" name="reason" required></textarea></div>
      </div></form>`);
      App.modal({ title: `Correct ${App.esc(r.pump_name)}`, body: form, actions: [{ label: "Cancel" }, {
        label: "Save correction", cls: "btn--primary",
        onClick: async () => {
          if (!Validation.validate(form)) return false;
          const d = App.formData(form);
          if (d.closing_meter === "") delete d.closing_meter;
          await App.put(`/api/shifts/${s.id}/readings/${r.pump_id}`, d);
          App.toast("Reading corrected (audit logged).", "ok"); reopen();
        },
      }] });
    });
    const mgrOnline = App.$("#mgr-online-save", body);
    if (mgrOnline) mgrOnline.addEventListener("click", () => App.run(async () => {
      await App.put(`/api/shifts/${s.id}/online`, { online_expression: App.$("#mgr-online", body).value, reason: "Entered by manager" });
      App.toast("Online money saved.", "ok");
      reopen();
    }));
    App.$("#deposit-form", body).addEventListener("submit", (e) => {
      e.preventDefault();
      if (!Validation.validate(e.target)) return;
      App.run(async () => {
        const res = await App.post(`/api/shifts/${s.id}/deposit`, App.formData(e.target));
        if (res.warning) {
          App.modal({ title: "⚠️ SHIFT MONEY DIFFERENCE", body: `<table class="pl-table">
            <tr><td>Expected</td><td>${App.money(res.warning.expected)}</td></tr>
            <tr><td>Actual</td><td>${App.money(res.warning.actual)}</td></tr>
            <tr class="total text-danger"><td>Difference</td><td>${App.signed(res.warning.difference, App.money)}</td></tr></table>`,
            actions: [{ label: "OK", cls: "btn--primary" }] });
        } else App.toast("Deposit recorded — matches expected money.", "ok");
        reopen();
      });
    });
  }

  await Promise.all([loadRoster(), loadList(), loadWeekPlan()]);
  if (params.get("shift")) openShift(params.get("shift"));
});
