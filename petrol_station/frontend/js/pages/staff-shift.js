/* ==========================================================================
   Staff: My Current Shift — start shift, opening/closing pump readings,
   refills, expected money and shift submission. Tank quantities are
   calculated automatically and shown to the manager only.
   ========================================================================== */
App.run(async () => {
  await App.init({ role: "STAFF", title: "My Shift" });
  const page = App.$("#page");
  let data = null;
  let categories = [];

  const editable = () => data.shift && ["OPEN", "FLAGGED"].includes(data.shift.status);

  function stepsHtml(t) {
    const step = (done, text) => `<div class="step ${done ? "done" : ""}">${done ? "✓" : "○"} ${text}</div>`;
    return `<div class="steps">
      ${step(t.started, "Shift started")}
      ${step(t.opening_readings, "Opening readings")}
      ${step(t.closing_readings, "Closing readings")}
      ${step(t.refills > 0, `Fuel refills (${t.refills})`)}
      ${step(t.submitted, "Shift submitted")}
    </div>`;
  }

  /** "4000+60000+5000" -> 69000 (same rule as the server). Returns null when invalid. */
  function addUp(text) {
    const t = (text || "").trim();
    if (!t) return 0;
    if (!/^[\d\s.,+]*$/.test(t)) return null;
    const parts = t.split("+").map((p) => p.replace(/[,\s]/g, ""));
    if (parts.some((p) => p === "" || isNaN(Number(p)))) return null;
    return parts.reduce((a, p) => a + Number(p), 0);
  }

  function moneyHtml(s) {
    const m = s.money;
    const dis = editable() ? "" : "disabled";
    return `<div class="card">
      <h3>Online money (mobile money, card, bank)</h3>
      <p class="small muted">Type each online payment and join them with <strong>+</strong>, e.g. <code>4000+60000+5000</code>.</p>
      <div class="row">
        <input class="input input--big" style="flex:1;min-width:0" id="online-input" inputmode="decimal"
               placeholder="4000+60000+5000" value="${App.esc(m.online_expression)}" ${dis}>
        ${editable() ? '<button class="btn btn--primary btn--lg" id="online-save">Save</button>' : ""}
      </div>
      <p class="reading-result"><span>= <strong id="online-preview">${App.money(m.online)}</strong></span></p>

      <h3 style="margin-top:1rem">Expenses paid from the cash this shift</h3>
      ${App.table({
        columns: [
          { key: "category", label: "Category" },
          { key: "description", label: "Description" },
          { key: "amount", label: "Amount", cls: "num", render: (e) => App.money(e.amount) },
          { key: "status", label: "Status", render: (e) => App.badge(e.status) },
          { key: "a", label: "", cls: "actions", render: (e) => e.status === "PENDING" && e.entered_by === App.user.id
            ? `<button class="btn btn--sm" data-edit-expense="${e.id}">Edit</button>` : "" },
        ], rows: m.expense_items, empty: "No expenses this shift.", cards: false })}
      ${editable() ? '<div class="form-actions"><button class="btn btn--lg" id="expense-add">+ Add expense</button></div>' : ""}

      <table class="pl-table" style="margin-top:1rem">
        <tr><td>Total (fuel sales)</td><td>${App.money(m.total)}</td></tr>
        <tr><td>− Online money</td><td>${App.money(m.online)}</td></tr>
        <tr class="total"><td>= Total cash (Total − Online)</td><td>${App.money(m.total_cash)}</td></tr>
        <tr><td>− Expenses</td><td>${App.money(m.expenses)}</td></tr>
        <tr class="total text-ok"><td>= Balance (cash to hand in)</td><td>${App.money(m.balance)}</td></tr>
      </table>
    </div>`;
  }

  /** Online money only: the night shift's online money (fixed) + this shift's own. No sales totals. */
  function onlineViewHtml(s) {
    const v = s.online_view;
    if (!v.previous) return "";
    return `<div class="card"><h3>Total money online</h3>
      <table class="pl-table">
        <tr><td>Online (${App.esc(v.previous.name.toLowerCase())} shift, ${App.date(v.previous.shift_date)}) <span class="small muted">— fixed</span></td>
            <td>${App.money(v.previous.online)}</td></tr>
        <tr><td>+ Online (this shift)</td><td>${App.money(v.own)}</td></tr>
        <tr class="total"><td>= Total money online</td><td>${App.money(v.total)}</td></tr>
      </table>
      <p class="small muted">This is only a sum of the online money. Your cash above uses only this shift's own online money.</p>
    </div>`;
  }

  function readingHtml(r) {
    const dis = editable() ? "" : "disabled";
    const mismatch = r.expected_opening_meter != null && Number(r.expected_opening_meter) !== Number(r.opening_meter) && r.opening_recorded_at;
    return `<div class="reading-card" style="border-left-color:${App.esc(r.fuel_color)}" data-pump="${r.pump_id}">
      <h3>${App.fuelDot(r.fuel_color)}${App.esc(r.pump_name)}
        ${r.closing_meter != null ? App.badge("ok", "Closed") : r.opening_recorded_at ? App.badge("info", "Opening saved") : App.badge("warn", "Opening needed")}</h3>
      ${r.expected_opening_meter != null ? `<p class="small muted">Previous closing meter: <strong>${App.num(r.expected_opening_meter, 3)}</strong></p>` : ""}
      ${mismatch ? '<p class="small text-danger"><strong>⚠️ Opening differs from the previous closing meter. The manager has been notified.</strong></p>' : ""}
      <div class="form-grid">
        <div class="field"><label>Opening meter reading</label>
          <input class="input input--big" type="number" inputmode="decimal" min="0" step="0.001" name="opening_meter" value="${r.opening_recorded_at ? r.opening_meter : (r.expected_opening_meter ?? "")}" ${dis}></div>
        <div class="field"><label>Closing meter reading</label>
          <input class="input input--big" type="number" inputmode="decimal" min="0" step="0.001" name="closing_meter" value="${r.closing_meter ?? ""}" ${dis}></div>
      </div>
      <div class="reading-result">
        <div>Fuel sold<br><strong data-sold>${App.litres(r.fuel_sold)}</strong></div>
        <div>Price / L<br><strong>${App.money(r.price_per_litre ?? r.current_price)}</strong></div>
        <div>Revenue<br><strong data-revenue>${App.money(r.revenue)}</strong></div>
      </div>
      ${editable() ? '<div class="form-actions"><button class="btn btn--primary btn--lg" data-save-reading>Save readings</button></div>' : ""}
    </div>`;
  }

  function render() {
    if (!data.shift) {
      page.innerHTML = `
        <div class="card"><h1>No shift right now</h1>
          <p>You are not assigned to the current shift (${App.esc(data.current_shift.label)}). Ask the manager to assign you.</p>
          ${data.my_shifts.length ? "<h3>Your upcoming shifts</h3>" + App.table({
            columns: [{ key: "label", label: "Shift" }, { key: "status", label: "Status", render: (r) => App.badge(r.status) }],
            rows: data.my_shifts }) : ""}
          <div class="form-actions"><a class="btn btn--primary btn--lg" href="/staff/refill.html">Record a fuel refill</a></div>
        </div>`;
      return;
    }
    const s = data.shift;
    const myPumps = s.readings.map((r) => r.pump_name).join(", ") || "All pumps";
    page.innerHTML = `
      <section class="shift-hero">
        <div class="small">MY CURRENT SHIFT ${data.is_current ? "" : "· (not the current time slot)"}</div>
        <h1>${App.esc(App.label(s.shift_type))} shift</h1>
        <div class="meta">
          <span>📅 ${App.date(s.shift_date)}</span>
          <span>🕒 ${App.localDateTime(s.scheduled_start).slice(11)} – ${App.localDateTime(s.scheduled_end).slice(11)}</span>
          <span>Status: <strong>${App.label(s.status)}</strong></span>
        </div>
        <div class="meta"><span>⛽ Assigned pumps: <strong>${App.esc(myPumps)}</strong></span></div>
        <div class="money"><span>Money expected from this shift (sales)</span><strong>${App.money(s.expected_money)}</strong>
          <span>${s.revenue_by_fuel.map((f) => `${App.esc(f.fuel)}: ${App.money(f.revenue)}`).join(" · ")}</span>
          <span style="display:block;margin-top:.4rem">Cash to hand in (after online money and expenses): <strong style="display:inline;font-size:1.1rem">${App.money(s.money.balance)}</strong></span></div>
      </section>
      ${s.status === "FLAGGED" ? `<div class="banner banner--danger"><div class="banner__icon">🚩</div><div class="banner__body"><h2>The manager flagged this shift</h2><p>${App.esc(s.flag_reason || "")}</p><p>Please correct the readings and submit again.</p></div></div>` : ""}
      ${s.status === "SUBMITTED" ? '<div class="banner banner--info"><div class="banner__icon">📨</div><div class="banner__body"><h2>Submitted</h2><p>Your shift is waiting for the manager\'s review. It can no longer be changed by you.</p></div></div>' : ""}
      ${s.status === "CLOSED" ? '<div class="banner banner--ok"><div class="banner__icon">✅</div><div class="banner__body"><h2>Shift closed</h2><p>The manager approved this shift.</p></div></div>' : ""}
      ${stepsHtml(s.tasks)}
      ${s.status === "SCHEDULED" ? `<div class="card center"><p>Start your shift to begin entering opening readings.</p>
        <button class="btn btn--primary btn--lg btn--block" id="start-btn">▶ START MY SHIFT</button></div>` : ""}
      ${s.status !== "SCHEDULED" ? `
        <h2 class="section-title">1. Pump meter readings</h2>
        <div class="stack">${s.readings.map(readingHtml).join("") || '<div class="empty">No pumps assigned.</div>'}</div>
        <div class="card"><h3>Sales per pump this shift</h3>
          <p class="small muted">Each pump has its own meter: litres dispensed = closing − opening.</p>
          ${App.table({
            columns: [
              { key: "pump_name", label: "Pump", render: (r) => App.fuelDot(r.fuel_color) + App.esc(r.pump_name) },
              { key: "fuel_sold", label: "Litres dispensed", cls: "num", render: (r) => r.closing_meter == null ? '<span class="muted">not closed</span>' : App.litres(r.fuel_sold) },
              { key: "revenue", label: "Amount", cls: "num", render: (r) => App.money(r.revenue) },
            ],
            rows: s.readings, cards: false,
            footer: [{ html: "Total" }, { cls: "num", html: App.litres(s.total_litres) }, { cls: "num", html: App.money(s.expected_money) }],
          })}
        </div>
        <h2 class="section-title">2. Fuel refills during this shift</h2>
        <div class="card">${App.table({
          columns: [
            { key: "fuel", label: "Fuel" },
            { key: "time", label: "Time", render: (r) => App.esc(String(r.refill_time).slice(0, 5)) },
            { key: "qty", label: "Quantity", cls: "num", render: (r) => App.litres(r.quantity_litres) },
            { key: "status", label: "Status", render: (r) => App.badge(r.status) },
          ], rows: s.refills, empty: "No refills recorded in this shift." })}
          <div class="form-actions"><a class="btn btn--lg" href="/staff/refill.html">🚚 Record a fuel refill</a></div></div>
        <h2 class="section-title">3. Money for this shift</h2>
        ${moneyHtml(s)}
        ${onlineViewHtml(s)}
        ${editable() ? `<h2 class="section-title">4. Finish</h2>
          <div class="card"><p>When all closing readings are entered, submit the shift for the manager's review.</p>
          <button class="btn btn--ok btn--lg btn--block" id="submit-btn">✔ SUBMIT MY SHIFT</button></div>` : ""}` : ""}`;
    wire();
  }

  function wire() {
    const start = App.$("#start-btn");
    if (start) start.addEventListener("click", () => App.run(async () => {
      const res = await App.post(`/api/shifts/${data.shift.id}/start`);
      data.shift = res.shift;
      App.toast("Shift started. Enter your opening readings.", "ok");
      render();
    }));

    App.$$("[data-pump]", page).forEach((card) => {
      const o = App.$('[name="opening_meter"]', card), c = App.$('[name="closing_meter"]', card);
      const reading = data.shift.readings.find((r) => String(r.pump_id) === card.dataset.pump);
      const price = Number(reading.price_per_litre ?? reading.current_price ?? 0);
      const live = () => {
        const { ok, sold } = Validation.meter(o, c);
        if (ok && sold !== null) {
          App.$("[data-sold]", card).textContent = App.litres(sold);
          App.$("[data-revenue]", card).textContent = App.money(sold * price);
        }
        return ok;
      };
      o.addEventListener("input", live);
      c.addEventListener("input", live);
      const btn = App.$("[data-save-reading]", card);
      if (btn) btn.addEventListener("click", () => App.run(async () => {
        if (!live()) { App.toast("INVALID METER READING — closing cannot be lower than opening.", "error"); return; }
        const payload = {};
        if (o.value !== "") payload.opening_meter = o.value;
        if (c.value !== "") payload.closing_meter = c.value;
        if (!Object.keys(payload).length) { App.toast("Enter a meter reading first", "error"); return; }
        btn.disabled = true;
        try {
          const res = await App.put(`/api/shifts/${data.shift.id}/readings/${card.dataset.pump}`, payload);
          data.shift = res.shift;
          App.toast("Readings saved.", "ok");
          render();
        } catch (err) {
          if (err.title) App.modal({ title: `⚠️ ${App.esc(err.title)}`, body: `<p>${App.esc(err.message)}</p>`, actions: [{ label: "OK", cls: "btn--primary" }] });
          else App.error(err);
        } finally { btn.disabled = false; }
      }));
    });

    const online = App.$("#online-input");
    if (online) online.addEventListener("input", () => {
      const v = addUp(online.value);
      App.$("#online-preview").textContent = v === null ? "check the numbers (use only digits and +)" : App.money(v);
      Validation.setError(online, v === null ? "Use only numbers and + (for example 4000+60000+5000)" : null);
    });
    const saveOnline = App.$("#online-save");
    if (saveOnline) saveOnline.addEventListener("click", () => App.run(async () => {
      if (addUp(online.value) === null) { online.focus(); return; }
      const res = await App.put(`/api/shifts/${data.shift.id}/online`, { online_expression: online.value });
      data.shift = res.shift;
      App.toast(`Online money saved: ${App.money(res.shift.money.online)}.`, "ok");
      render();
    }));

    const openExpense = (expense) => {
      const form = App.expenseForm(expense, categories);
      form.appendChild(App.h('<p class="small muted">Paid from the shift\'s cash. It is taken off the balance and sent to the manager for approval. You can edit it until the manager decides.</p>'));
      App.modal({ title: expense ? "Edit expense" : "Add expense", body: form, actions: [{ label: "Cancel" }, {
        label: "Save expense", cls: "btn--primary",
        onClick: async () => {
          if (!Validation.validate(form)) return false;
          const d = await App.expenseData(form);
          if (expense) await App.put(`/api/expenses/${expense.id}`, d);
          else await App.post("/api/expenses", { ...d, shift_id: data.shift.id });
          data.shift = (await App.get(`/api/shifts/${data.shift.id}`)).shift;
          categories = (await App.get("/api/expense-categories")).categories;   // includes any new word just typed
          App.toast(expense ? "Expense updated." : "Expense saved.", "ok");
          render();
        },
      }] });
    };
    const addExpense = App.$("#expense-add");
    if (addExpense) addExpense.addEventListener("click", () => openExpense(null));
    App.$$("[data-edit-expense]", page).forEach((btn) => btn.addEventListener("click", () =>
      openExpense(data.shift.money.expense_items.find((e) => String(e.id) === btn.dataset.editExpense))));

    const submit = App.$("#submit-btn");
    if (submit) submit.addEventListener("click", () => App.run(async () => {
      const ok = await App.ask({ title: "Submit shift?", message: `Money expected from this shift: <strong>${App.money(data.shift.expected_money)}</strong>. After submitting you cannot change the readings.`, confirmLabel: "Yes, submit" });
      if (!ok) return;
      const res = await App.post(`/api/shifts/${data.shift.id}/submit`);
      data.shift = res.shift;
      App.toast("Shift submitted for manager review.", "ok");
      render();
    }));
  }

  const [shiftData, { categories: allCategories }] = await Promise.all([App.get("/api/staff/my-shift"), App.get("/api/expense-categories")]);
  categories = allCategories;
  data = shiftData;
  render();
});
