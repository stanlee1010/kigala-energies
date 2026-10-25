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

      <div id="exp-sheet" style="margin-top:1rem"></div>

      <table class="pl-table" style="margin-top:1rem">
        <tr><td>Total (fuel sales)</td><td>${App.money(m.total)}</td></tr>
        <tr><td>− Online money</td><td>${App.money(m.online)}</td></tr>
        <tr class="total"><td>= Total cash (Total − Online)</td><td>${App.money(m.total_cash)}</td></tr>
        <tr><td>− Expenses</td><td>${App.money(m.expenses)}</td></tr>
        <tr class="total text-ok"><td>= Balance (cash to hand in)</td><td>${App.money(m.balance)}</td></tr>
      </table>
    </div>`;
  }

  /** Online money only: the previous shift's online money exactly as its staff typed it (fixed)
   *  + this shift's own. No sales totals. */
  function onlineViewHtml(s) {
    const v = s.online_view;
    if (!v.previous) return "";
    const typed = (expr, total) => (expr ? `<code>${App.esc(expr.split("+").join(" + "))}</code> = ` : "") +
      `<strong>${App.money(total)}</strong>`;
    return `<div class="card"><h3>Online money history</h3>
      <table class="pl-table">
        <tr><td>Previous shift — ${App.esc(v.previous.name)}, ${App.date(v.previous.shift_date)}
              <span class="small muted">(as written by that shift, fixed)</span><br>${typed(v.previous.online_expression, v.previous.online)}</td>
            <td>${App.money(v.previous.online)}</td></tr>
        <tr><td>+ This shift<br>${typed(v.own_expression, v.own)}</td><td>${App.money(v.own)}</td></tr>
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

  /* Several people on one shift: each ticks the pumps they work on (a pump is one person's). */
  function pumpPickerHtml(choice) {
    const picked = new Set(choice.mine.length ? choice.mine : choice.suggested);
    const others = choice.people.filter((p) => p.pumps.length && p.name !== App.user.full_name).map((p) => p.name);
    return `<div class="pump-picker">
      <p class="small"><strong>${choice.needed} people work this shift.</strong> Tick the pumps you will work on${others.length ? ` (taken pumps show who has them)` : ""}:</p>
      <div class="pump-picker__list">${choice.pumps.map((p) => `
        <label class="pump-choice ${p.taken_by ? "is-taken" : ""}">
          <input type="checkbox" name="my-pump" value="${p.id}" ${p.taken_by ? "disabled" : ""} ${picked.has(p.id) && !p.taken_by ? "checked" : ""}>
          ${App.esc(p.name)}${p.taken_by ? ` <span class="small muted">— ${App.esc(p.taken_by)}</span>` : ""}</label>`).join("")}</div>
    </div>`;
  }
  const pickedPumps = () => App.$$('[name="my-pump"]:checked', page).map((c) => Number(c.value));
  const needsPicker = () => data.pump_choice && data.pump_choice.needed > 1;

  async function reloadShift(message) {
    data = await App.get("/api/staff/my-shift");
    if (message) App.toast(message, "ok");
    render();
  }

  function render() {
    if (!data.shift) {
      const cur = data.current_shift, prev = data.previous_shift;
      const hours = `${App.localDateTime(cur.scheduled_start).slice(11)} – ${App.localDateTime(cur.scheduled_end).slice(11)}`;
      page.innerHTML = `
        <div class="card center start-shift">
          <div class="small muted">SHIFT RUNNING NOW</div>
          <h1>${App.esc(cur.label)}</h1>
          <p>🕒 ${App.esc(hours)}${cur.staff.length ? ` · On shift: <strong>${App.esc(cur.staff.join(", "))}</strong>` : ""}</p>
          ${prev ? `<p class="small muted">Previous shift: ${App.esc(prev.label)} — ${App.label(prev.status)}${prev.staff.length ? ` (${App.esc(prev.staff.join(", "))})` : ""}.
            Your opening meters start from its closing readings.</p>` : ""}
          ${cur.can_start
            ? `${needsPicker() ? pumpPickerHtml(data.pump_choice) : ""}
               <button class="btn btn--primary btn--lg btn--block" id="take-btn">▶ START SHIFT</button>
               <p class="small muted">${cur.status === "OPEN" ? "This shift is already open — you will join it." : "You will be put on this shift and it will open."}</p>`
            : ["SCHEDULED", "OPEN"].includes(cur.status)
              ? `<p>The ${App.esc(cur.label)} shift already has its ${cur.staff_needed} ${cur.staff_needed === 1 ? "person" : "people"}${cur.staff.length ? ` (${App.esc(cur.staff.join(", "))})` : ""}. Ask the manager if you should work too.</p>`
              : `<p>The ${App.esc(cur.label)} shift is ${App.label(cur.status).toLowerCase()}. Ask the manager if you need to work on it.</p>`}
        </div>
        <div class="card center">
          <a class="btn btn--lg btn--block" href="/staff/refill.html">🚚 Record a fuel refill</a>
        </div>`;
      const take = App.$("#take-btn");
      if (take) take.addEventListener("click", () => App.run(async () => {
        if (needsPicker() && !pickedPumps().length) { App.toast("Tick the pumps you will work on.", "error"); return; }
        await App.post("/api/staff/start-shift", { pump_ids: needsPicker() ? pickedPumps() : [] });
        await reloadShift("Shift started. Enter your opening readings.");
      }));
      return;
    }
    const s = data.shift;
    const choosing = needsPicker() && data.pump_choice.must_choose;
    const myPumps = choosing ? "not chosen yet" : s.readings.map((r) => r.pump_name).join(", ") || "All pumps";
    const coworkers = needsPicker() ? data.pump_choice.people.filter((p) => p.name !== App.user.full_name).map((p) => p.name) : [];
    page.innerHTML = `
      <section class="shift-hero">
        <div class="small">MY CURRENT SHIFT ${data.is_current ? "" : "· (not the current time slot)"}</div>
        <h1>${App.esc(App.label(s.shift_type))} shift</h1>
        <div class="meta">
          <span>📅 ${App.date(s.shift_date)}</span>
          <span>🕒 ${App.localDateTime(s.scheduled_start).slice(11)} – ${App.localDateTime(s.scheduled_end).slice(11)}</span>
          <span>Status: <strong>${App.label(s.status)}</strong></span>
        </div>
        <div class="meta"><span>⛽ My pumps: <strong>${App.esc(myPumps)}</strong></span>
          ${needsPicker() && !choosing && ["OPEN", "FLAGGED", "SCHEDULED"].includes(s.status) ? '<button type="button" class="btn btn--sm" id="change-pumps">Change my pumps</button>' : ""}
          ${coworkers.length ? `<span>👥 Also on this shift: <strong>${App.esc(coworkers.join(", "))}</strong></span>` : ""}</div>
        <div class="money"><span>Money expected from this shift (sales)</span><strong>${App.money(s.expected_money)}</strong>
          <span>${s.revenue_by_fuel.map((f) => `${App.esc(f.fuel)}: ${App.money(f.revenue)}`).join(" · ")}</span>
          <span style="display:block;margin-top:.4rem">Cash to hand in (after online money and expenses): <strong style="display:inline;font-size:1.1rem">${App.money(s.money.balance)}</strong></span></div>
      </section>
      ${s.status === "FLAGGED" ? `<div class="banner banner--danger"><div class="banner__icon">🚩</div><div class="banner__body"><h2>The manager flagged this shift</h2><p>${App.esc(s.flag_reason || "")}</p><p>Please correct the readings and submit again.</p></div></div>` : ""}
      ${s.status === "SUBMITTED" ? '<div class="banner banner--info"><div class="banner__icon">📨</div><div class="banner__body"><h2>Submitted</h2><p>Your shift is waiting for the manager\'s review. It can no longer be changed by you.</p></div></div>' : ""}
      ${s.status === "CLOSED" ? '<div class="banner banner--ok"><div class="banner__icon">✅</div><div class="banner__body"><h2>Shift closed</h2><p>The manager approved this shift.</p></div></div>' : ""}
      ${stepsHtml(s.tasks)}
      ${s.status === "SCHEDULED" ? `<div class="card center"><p>Start your shift to begin entering opening readings.</p>
        ${needsPicker() ? pumpPickerHtml(data.pump_choice) : ""}
        <button class="btn btn--primary btn--lg btn--block" id="start-btn">▶ START MY SHIFT</button></div>` : ""}
      ${choosing && ["OPEN", "FLAGGED"].includes(s.status) ? `<div class="card center choose-pumps"><h2>Choose your pumps</h2>
        ${pumpPickerHtml(data.pump_choice)}
        <button class="btn btn--primary btn--lg btn--block" id="save-pumps">Save my pumps</button></div>` : ""}
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

  /** Show what is saved on this device but not sent yet (e.g. after reopening the page offline). */
  function markPending(card) {
    if (!App.$(".pending-note", card)) card.appendChild(App.h('<p class="small pending-note">⏳ Saved on this device — waiting for the internet to send.</p>'));
  }
  function applyPending() {
    App.pending(`/api/shifts/${data.shift.id}/readings/`).forEach((item) => {
      const card = App.$(`[data-pump="${item.url.split("/").pop()}"]`, page);
      if (!card) return;
      if (item.data.opening_meter !== undefined) App.$('[name="opening_meter"]', card).value = item.data.opening_meter;
      if (item.data.closing_meter !== undefined) App.$('[name="closing_meter"]', card).value = item.data.closing_meter;
      markPending(card);
    });
    const online = App.pending(`/api/shifts/${data.shift.id}/online`)[0];
    if (online && App.$("#online-input")) {
      App.$("#online-input").value = online.data.online_expression;
      App.$("#online-input").dispatchEvent(new Event("input"));
    }
  }

  function wire() {
    if (data.shift) applyPending();
    const start = App.$("#start-btn");
    if (start) start.addEventListener("click", () => App.run(async () => {
      if (needsPicker() && !pickedPumps().length) { App.toast("Tick the pumps you will work on.", "error"); return; }
      await App.post(`/api/shifts/${data.shift.id}/start`, { pump_ids: needsPicker() ? pickedPumps() : [] });
      await reloadShift("Shift started. Enter your opening readings.");
    }));
    const savePumps = App.$("#save-pumps");
    if (savePumps) savePumps.addEventListener("click", () => App.run(async () => {
      if (!pickedPumps().length) { App.toast("Tick the pumps you will work on.", "error"); return; }
      await App.put(`/api/shifts/${data.shift.id}/my-pumps`, { pump_ids: pickedPumps() });
      await reloadShift("Your pumps are saved.");
    }));
    const changePumps = App.$("#change-pumps");
    if (changePumps) changePumps.addEventListener("click", () => {
      const form = App.h(`<div>${pumpPickerHtml(data.pump_choice)}</div>`);
      App.modal({ title: "Change my pumps", body: form, actions: [{ label: "Cancel" }, {
        label: "Save", cls: "btn--primary",
        onClick: async () => {
          const ids = App.$$('[name="my-pump"]:checked', form).map((c) => Number(c.value));
          if (!ids.length) { App.toast("Tick at least one pump.", "error"); return false; }
          await App.put(`/api/shifts/${data.shift.id}/my-pumps`, { pump_ids: ids });
          await reloadShift("Your pumps are saved.");
        },
      }] });
    });

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
        // What the reading was when this page got it: the server refuses to overwrite a newer change.
        payload.based_on = { opening_meter: reading.opening_recorded_at ? reading.opening_meter : null,
                             closing_meter: reading.closing_meter };
        btn.disabled = true;
        try {
          const res = await App.put(`/api/shifts/${data.shift.id}/readings/${card.dataset.pump}`, payload);
          if (res.queued) { App.savedOffline(); markPending(card); return; }
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
      const res = await App.put(`/api/shifts/${data.shift.id}/online`, { online_expression: online.value,
        based_on: { online_expression: data.shift.money.online_expression } });
      if (res.queued) { App.savedOffline(); return; }
      data.shift = res.shift;
      App.toast(`Online money saved: ${App.money(res.shift.money.online)}.`, "ok");
      render();
    }));

    const sheet = App.$("#exp-sheet");
    if (sheet) App.expenseSheet(sheet, {
      title: "Matumizi — expenses paid from the cash this shift",
      items: withPendingLines(data.shift.money.expense_items, `/api/shifts/${data.shift.id}/expenses`), editable: editable(), categories,
      save: async (lines) => {
        const res = await App.put(`/api/shifts/${data.shift.id}/expenses`, { lines });
        if (res.queued) return res;
        data.shift = (await App.get(`/api/shifts/${data.shift.id}`)).shift;
        categories = (await App.get("/api/expense-categories")).categories;   // new words become suggestions
        render();
        return res;
      },
    });

    const submit = App.$("#submit-btn");
    if (submit) submit.addEventListener("click", () => App.run(async () => {
      // Saves made without internet must reach the server first, or the shift would be
      // submitted without them (and they would then be refused).
      if (App.pending("/api/").length) await App.flushOutbox();
      if (App.pending("/api/").length) {
        App.toast("Some changes saved without internet have not been sent yet. Wait for the internet (see the bar at the bottom), then submit.", "error", 10000);
        return;
      }
      const ok = await App.ask({ title: "Submit shift?", message: `Money expected from this shift: <strong>${App.money(data.shift.expected_money)}</strong>. After submitting you cannot change the readings.`, confirmLabel: "Yes, submit" });
      if (!ok) return;
      const res = await App.post(`/api/shifts/${data.shift.id}/submit`);
      data.shift = res.shift;
      App.toast("Shift submitted for manager review.", "ok");
      render();
    }));
  }

  // When saves made without internet have been sent, show the fresh figures.
  document.addEventListener("outbox:sent", () => App.run(async () => {
    data = await App.get("/api/staff/my-shift");
    render();
  }));

  const [shiftData, { categories: allCategories }] = await Promise.all([
    App.get("/api/staff/my-shift"), App.get("/api/expense-categories")]);
  categories = allCategories;
  data = shiftData;
  render();
});
