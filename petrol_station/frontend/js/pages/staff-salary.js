/* MY SALARY — a staff member's salary, the money they took and what remains (read-only).
   The manager keeps these figures on the Salaries page; this page shows the latest. */
App.run(async () => {
  await App.init({ role: "STAFF", title: "My Salary" });
  const page = App.$("#page");
  let month = App.serverToday().slice(0, 7);

  const monthName = (m) => new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${m}-01T00:00:00Z`));

  page.innerHTML = `
    <div class="page-head"><div><h1>My salary</h1></div>
      <div class="field"><label for="month">Month</label><input class="input" type="month" id="month" value="${month}" max="${month}"></div></div>
    <div id="summary"></div>
    <div class="card"><div class="card__head"><h2>Money taken</h2></div><div id="list"></div></div>`;

  async function load() {
    const { row: r } = await App.get(`/api/staff/my-salary${App.qs({ month })}`);
    App.$("#summary").innerHTML = `
      <div class="card"><div class="card__head"><h2>${monthName(month)}</h2></div>
        ${App.table({ rows: [r], columns: [
          { key: "salary", label: "Salary", cls: "num", render: () => r.salary === null ? '<span class="muted">Not set yet</span>' : App.money(r.salary) },
          { key: "owed_before", label: "Owed from last month", cls: "num", render: () => Number(r.owed_before) > 0
              ? `<span class="text-danger">− ${App.money(r.owed_before)}</span>` : "—" },
          { key: "taken", label: "Money taken", cls: "num", render: () => App.money(r.taken) },
          { key: "remaining", label: "Remaining", cls: "num", render: () => `<strong class="${Number(r.remaining) < 0 ? "text-danger" : "text-ok"}">${App.money(r.remaining)}</strong>`
              + (Number(r.remaining) < 0 ? '<div class="small text-danger">taken from next month</div>' : "") },
        ] })}</div>`;
    App.$("#list").innerHTML = App.table({ rows: r.advances, empty: "You have not taken any money this month.", columns: [
      { key: "taken_on", label: "Date", render: (a) => App.date(a.taken_on) },
      { key: "amount", label: "Amount", cls: "num", render: (a) => a.status === "VOID" ? `<s>${App.money(a.amount)}</s>` : App.money(a.amount) },
      { key: "note", label: "Note", render: (a) => App.esc(a.note || "—") },
      { key: "status", label: "Status", render: (a) => a.status === "VOID"
          ? `${App.badge("VOID", "Cancelled")}<div class="small muted">${App.esc(a.void_reason || "")}</div>` : App.badge("APPROVED", "Deducted") },
    ] });
  }

  App.$("#month").addEventListener("change", (e) => App.run(async () => {
    if (!/^\d{4}-\d{2}$/.test(e.target.value)) return;
    month = e.target.value;
    await load();
  }));

  await load();
  setInterval(() => App.run(load), 60 * 1000);   // the manager's changes show up without reloading
});
