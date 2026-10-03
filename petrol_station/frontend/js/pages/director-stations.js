/* DIRECTOR — add, edit, remove (archive) and restore locations (stations). */
App.run(async () => {
  await App.init({ role: "DIRECTOR", title: "Stations" });
  const page = App.$("#page");
  page.innerHTML = `
    <div class="page-head"><div><h1>Stations</h1>
      <p>Add a location — it starts with the standard set-up (pumps, tanks, Morning/Night shifts) and works exactly like the others.
         Remove a location to take it out of use; its history is always kept and it can be restored.</p></div>
      <button class="btn btn--primary btn--lg" id="add-btn">+ Add station</button></div>
    <div class="kpis" id="counts"></div>
    <div class="card"><h2>Stations in use</h2><div id="active"></div></div>
    <div class="card"><h2>Removed stations</h2>
      <p class="small muted">Removed stations do not appear for managers or in the totals, and their staff cannot log in.
         All their sales, money and audit records are kept.</p>
      <div id="removed"></div></div>`;

  let stations = [], managers = [];

  function open(s) {
    const f = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Station name</label><input class="input" name="name" value="${App.esc(s ? s.name : "")}" placeholder="e.g. Kigala Mbezi" required></div>
      <div class="field"><label>Address / area</label><input class="input" name="address" value="${App.esc(s ? s.address || "" : "")}"></div>
      <div class="field"><label>Phone</label><input class="input" name="phone" value="${App.esc(s ? s.phone || "" : "")}"></div>
      ${s ? "" : `<div class="field span-all"><label>Managers who will run it</label>
        <div class="row">${managers.filter((m) => m.is_active).map((m) => `
          <label class="checkbox"><input type="checkbox" name="manager" value="${m.id}"> ${App.esc(m.full_name)}</label>`).join("") || '<span class="muted small">No managers yet — add them on the Managers page.</span>'}</div>
        <span class="hint">You can also give managers access later on the Managers page.</span></div>`}
    </div></form>`);
    App.modal({ title: s ? `Edit ${App.esc(s.name)}` : "Add station", body: f, actions: [{ label: "Cancel" }, {
      label: s ? "Save" : "Add station", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(f)) return false;
        const d = App.formData(f);
        d.manager_ids = App.$$('[name="manager"]:checked', f).map((c) => Number(c.value));
        delete d.manager;
        if (s) await App.put(`/api/director/stations/${s.id}`, d); else await App.post("/api/director/stations", d);
        App.toast(s ? "Station updated." : `${d.name} added. Its manager can now set prices, tanks and staff.`, "ok", 7000);
        load();
      },
    }] });
  }

  const columns = (active) => [
    { key: "name", label: "Station", render: (s) => `<strong>📍 ${App.esc(s.name)}</strong><div class="small muted">${App.esc(s.address || "")} · ${App.esc(s.code)}</div>` },
    { key: "managers", label: "Managers", cls: "num" },
    { key: "staff", label: "Staff", cls: "num" },
    { key: "pumps", label: "Pumps", cls: "num" },
    { key: "a", label: "", cls: "actions", render: (s) => active
      ? `<button class="btn btn--sm" data-edit="${s.id}">Edit</button>
         <button class="btn btn--sm btn--danger" data-remove="${s.id}">Remove</button>`
      : `<button class="btn btn--sm btn--ok" data-restore="${s.id}">Restore</button>` },
  ];

  async function load() {
    [{ stations }, { managers }] = await Promise.all([App.get("/api/director/stations"), App.get("/api/director/managers")]);
    const active = stations.filter((s) => s.is_active), removed = stations.filter((s) => !s.is_active);
    App.$("#counts").innerHTML = `
      <div class="kpi"><div class="kpi__label">Stations in use</div><div class="kpi__value">${active.length}</div></div>
      <div class="kpi"><div class="kpi__label">Removed (archived)</div><div class="kpi__value">${removed.length}</div></div>`;
    App.$("#active").innerHTML = App.table({ columns: columns(true), rows: active });
    App.$("#removed").innerHTML = App.table({ columns: columns(false), rows: removed, empty: "No removed stations." });
  }

  App.$("#add-btn").addEventListener("click", () => open(null));
  App.on(page, "click", "[data-edit]", (e, b) => open(stations.find((s) => String(s.id) === b.dataset.edit)));
  App.on(page, "click", "[data-remove]", (e, b) => App.run(async () => {
    const s = stations.find((x) => String(x.id) === b.dataset.remove);
    const reason = await App.ask({
      title: `Remove ${App.esc(s.name)}?`, danger: true, confirmLabel: "Remove station",
      message: `<strong>${App.esc(s.name)}</strong> will be taken out of use: it disappears for managers and from the totals, and its ${s.staff} staff can no longer log in. All its records are kept and you can restore it at any time.`,
      input: { label: "Reason (optional)", required: false } });
    if (reason === false) return;
    await App.post(`/api/director/stations/${s.id}/remove`, { reason });
    App.toast(`${s.name} removed.`, "ok");
    load();
  }));
  App.on(page, "click", "[data-restore]", (e, b) => App.run(async () => {
    const s = stations.find((x) => String(x.id) === b.dataset.restore);
    await App.post(`/api/director/stations/${s.id}/restore`, {});
    App.toast(`${s.name} restored — its managers and staff can use it again.`, "ok");
    load();
  }));

  await load();
});
