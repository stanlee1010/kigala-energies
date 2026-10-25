/* SALARIES — the manager types each staff member's salary and records money they take.
   Money taken is deducted from the salary of the month it was taken in (Remaining = Salary − Money taken).
   Staff see their own row on their "My Salary" page. */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Salaries" });
  const page = App.$("#page");
  const params = new URLSearchParams(location.search);
  const thisMonth = App.serverToday().slice(0, 7);
  let month = /^\d{4}-(0[1-9]|1[0-2])$/.test(params.get("month") || "") ? params.get("month") : thisMonth;
  let rows = [];

  const monthName = (m) => new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${m}-01T00:00:00Z`));
  const remainingHtml = (v) => `<strong class="${Number(v) < 0 ? "text-danger" : ""}">${App.money(v)}</strong>`;
  const owedHtml = (v) => Number(v) > 0 ? `<span class="text-danger">− ${App.money(v)}</span>` : '<span class="muted">—</span>';

  page.innerHTML = `
    <div class="page-head"><div><h1>Salaries</h1>
      <p>The same salary shows every month until you change it. Money a staff member takes is deducted from that
        month's salary; if more is taken than the salary, the rest is deducted from next month's salary.
        Staff see the same figures on their <strong>My Salary</strong> page.</p></div>
      <div class="field"><label for="month">Month</label><input class="input" type="month" id="month" value="${month}"></div></div>
    <div class="card"><div class="card__head"><h2 id="title"></h2></div><div id="list"></div></div>`;

  function render() {
    App.$("#title").textContent = `Salaries — ${monthName(month)}`;
    const total = (k) => rows.reduce((a, r) => a + Number(r[k] || 0), 0);
    App.$("#list").innerHTML = App.table({
      rows,
      empty: "No staff yet. Create staff accounts on the Staff page.",
      columns: [
        { key: "full_name", label: "Staff", render: (r) => `<strong>${App.esc(r.full_name)}</strong>
            <div class="small muted">${App.esc(r.username)}${r.is_active ? "" : " · disabled"}</div>` },
        { key: "salary", label: "Salary", cls: "num", render: (r) => `
            <input class="input input--sm salary-input" type="number" inputmode="decimal" min="0" step="any"
              data-salary="${r.user_id}" value="${r.salary ?? ""}" placeholder="Type salary" aria-label="Salary for ${App.esc(r.full_name)}">
            ${r.salary_carried ? '<div class="small muted">same as last month</div>' : ""}` },
        { key: "owed_before", label: "Owed from last month", cls: "num", render: (r) => owedHtml(r.owed_before) },
        { key: "taken", label: "Money taken", cls: "num", render: (r) => {
            const n = r.advances.filter((a) => a.status === "ACTIVE").length;
            return `${App.money(r.taken)}${n ? `<div class="small"><button type="button" class="link-btn" data-history="${r.user_id}">${n} ${n === 1 ? "entry" : "entries"}</button></div>` : ""}`;
          } },
        { key: "remaining", label: "Remaining", cls: "num", render: (r) => remainingHtml(r.remaining)
            + (Number(r.remaining) < 0 ? '<div class="small text-danger">taken from next month</div>' : "") },
        { key: "actions", label: "", render: (r) => `<div class="row">
            <button type="button" class="btn btn--sm btn--primary" data-take="${r.user_id}">− Money taken</button>
            <button type="button" class="btn btn--sm" data-history="${r.user_id}">History</button></div>` },
      ],
      footer: rows.length ? [
        { html: "<strong>Total</strong>" },
        { html: `<strong>${App.money(total("salary"))}</strong>`, cls: "num", label: "Salaries" },
        { html: total("owed_before") ? `<strong class="text-danger">− ${App.money(total("owed_before"))}</strong>` : "—", cls: "num", label: "Owed from last month" },
        { html: `<strong>${App.money(total("taken"))}</strong>`, cls: "num", label: "Money taken" },
        { html: remainingHtml(total("remaining")), cls: "num", label: "Remaining" },
        { html: "" },
      ] : null,
    });
  }

  async function load() {
    ({ rows } = await App.get(`/api/salaries${App.qs({ month })}`));
    render();
  }

  const rowOf = (uid) => rows.find((r) => r.user_id === Number(uid));

  // Salary: typed in the table, saved when the box is left (or Enter is pressed).
  App.on(App.$("#list"), "change", "[data-salary]", (e, input) => App.run(async () => {
    const r = rowOf(input.dataset.salary);
    if (input.value === "" || Number(input.value) < 0) { App.toast("Type the salary (0 or more).", "error"); input.value = r.salary ?? ""; return; }
    const res = await App.put(`/api/salaries/${r.user_id}`, { month, salary: input.value });
    if (res.queued) { App.toast("No internet — the salary will be saved when the connection is back.", "info"); return; }
    App.toast(`Salary saved for ${r.full_name}.`, "ok");
    await load();
  }));
  App.on(App.$("#list"), "keydown", "[data-salary]", (e, input) => { if (e.key === "Enter") input.blur(); });

  // Money taken: deducted from the salary of the month of the date.
  App.on(App.$("#list"), "click", "[data-take]", (e, b) => {
    const r = rowOf(b.dataset.take);
    const today = App.serverToday();
    const day = month === thisMonth ? today : (month < thisMonth ? `${month}-01` : today);
    const form = App.h(`<form novalidate class="stack">
      <p>Money taken by <strong>${App.esc(r.full_name)}</strong>. It is deducted from the salary of the month of the date.</p>
      <div class="form-grid">
        <div class="field"><label>Amount taken (${App.esc(App.settings.currency)})</label>
          <input class="input input--big" type="number" inputmode="decimal" min="0" step="any" name="amount" required></div>
        <div class="field"><label>Date taken</label><input class="input" type="date" name="taken_on" value="${day}" max="${today}" required></div>
        <div class="field span-all"><label>Note (optional)</label><input class="input" name="note" maxlength="255" placeholder="e.g. School fees, transport"></div>
      </div>
      <p class="small" data-after></p></form>`);
    const after = App.$("[data-after]", form);
    const showAfter = () => {
      const amount = Number(App.$('[name="amount"]', form).value || 0);
      const sameMonth = App.$('[name="taken_on"]', form).value.slice(0, 7) === month;
      after.innerHTML = sameMonth && amount > 0
        ? `Remaining after this: ${remainingHtml(Number(r.remaining) - amount)}` : "";
    };
    form.addEventListener("input", showAfter);
    App.modal({ title: "− Money taken from salary", body: form, actions: [{ label: "Cancel" }, {
      label: "Save", cls: "btn--primary",
      onClick: async () => {
        const d = App.formData(form);
        if (!(Number(d.amount) > 0)) { App.toast("Type the amount taken.", "error"); return false; }
        if (!d.taken_on || d.taken_on > today) { App.toast("Choose a date that is not in the future.", "error"); return false; }
        if (d.taken_on.slice(0, 7) === month && Number(d.amount) > Number(r.remaining) && Number(r.salary) > 0) {
          const ok = await App.ask({ title: "More than the remaining salary", confirmLabel: "Save anyway",
            message: `${App.esc(r.full_name)} has ${App.money(r.remaining)} remaining this month. Save ${App.money(d.amount)} anyway?` });
          if (!ok) return false;
        }
        await App.post(`/api/salaries/${r.user_id}/advances`, d);
        App.toast(`Saved — deducted from ${App.esc(r.full_name)}'s salary.`, "ok");
        await load();
      },
    }] });
  });

  // History: every entry of the month; a wrong one is cancelled with a reason (never deleted).
  function showHistory(uid) {
    const r = rowOf(uid);
    const body = App.h(`<div class="stack">
      <div class="kpis">
        <div class="kpi"><div class="kpi__label">Salary</div><div class="kpi__value">${App.money(r.salary)}</div></div>
        ${Number(r.owed_before) > 0 ? `<div class="kpi kpi--loss"><div class="kpi__label">Owed from last month</div><div class="kpi__value">− ${App.money(r.owed_before)}</div></div>` : ""}
        <div class="kpi"><div class="kpi__label">Money taken</div><div class="kpi__value">${App.money(r.taken)}</div></div>
        <div class="kpi ${Number(r.remaining) < 0 ? "kpi--loss" : "kpi--profit"}"><div class="kpi__label">Remaining</div><div class="kpi__value">${App.money(r.remaining)}</div></div>
      </div>
      ${App.table({ rows: r.advances, empty: "No money taken this month.", columns: [
        { key: "taken_on", label: "Date", render: (a) => App.date(a.taken_on) },
        { key: "amount", label: "Amount", cls: "num", render: (a) => a.status === "VOID" ? `<s>${App.money(a.amount)}</s>` : App.money(a.amount) },
        { key: "note", label: "Note", render: (a) => App.esc(a.note || "—") },
        { key: "recorded_by", label: "Recorded by", render: (a) => App.esc(a.recorded_by || "—") },
        { key: "status", label: "Status", render: (a) => a.status === "VOID"
            ? `${App.badge("VOID", "Cancelled")}<div class="small muted">${App.esc(a.void_reason || "")}</div>` : App.badge("APPROVED", "Deducted") },
        { key: "act", label: "", render: (a) => a.status === "ACTIVE" ? `<button type="button" class="btn btn--sm btn--danger" data-void="${a.id}">Cancel</button>` : "" },
      ] })}</div>`);
    const m = App.modal({ title: `${App.esc(r.full_name)} — ${monthName(month)}`, body, wide: true, actions: [{ label: "Close" }] });
    App.on(body, "click", "[data-void]", (e, b) => App.run(async () => {
      const reason = await App.ask({ title: "Cancel this entry?", confirmLabel: "Cancel entry", danger: true,
        message: "The amount is no longer deducted. The entry stays in the history as cancelled.",
        input: { label: "Reason", required: true } });
      if (!reason) return;
      await App.post(`/api/salary-advances/${b.dataset.void}/void`, { reason });
      App.toast("Entry cancelled.", "ok");
      m.close();
      await load();
      showHistory(uid);
    }));
  }
  App.on(App.$("#list"), "click", "[data-history]", (e, b) => showHistory(b.dataset.history));

  App.$("#month").addEventListener("change", (e) => App.run(async () => {
    if (!/^\d{4}-\d{2}$/.test(e.target.value)) return;
    month = e.target.value;
    history.replaceState(null, "", `?month=${month}`);
    await load();
  }));

  await load();
});
