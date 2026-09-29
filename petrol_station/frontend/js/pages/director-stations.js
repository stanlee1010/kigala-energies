/* DIRECTOR — the locations (stations). */
App.run(async () => {
  await App.init({ role: "DIRECTOR", title: "Stations" });
  const page = App.$("#page");
  page.innerHTML = `
    <div class="page-head"><div><h1>Stations</h1>
      <p>Add a new location. It starts with the standard set-up (pumps, tanks, Morning/Night shifts); its manager then adjusts it.</p></div>
      <button class="btn btn--primary" id="add-btn">+ Add station</button></div>
    <div class="card" id="list"></div>`;

  let stations = [], managers = [];

  function open(s) {
    const f = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Station name</label><input class="input" name="name" value="${App.esc(s ? s.name : "")}" placeholder="e.g. Kigala Mbezi" required></div>
      <div class="field"><label>Address / area</label><input class="input" name="address" value="${App.esc(s ? s.address || "" : "")}"></div>
      <div class="field"><label>Phone</label><input class="input" name="phone" value="${App.esc(s ? s.phone || "" : "")}"></div>
      ${s ? "" : `<div class="field span-all"><label>Managers who will run it</label>
        <div class="row">${managers.filter((m) => m.is_active).map((m) => `
          <label class="checkbox"><input type="checkbox" name="manager" value="${m.id}"> ${App.esc(m.full_name)}</label>`).join("") || '<span class="muted small">No managers yet — add them on the Managers page.</span>'}</div></div>`}
    </div></form>`);
    App.modal({ title: s ? `Edit ${App.esc(s.name)}` : "Add station", body: f, actions: [{ label: "Cancel" }, {
      label: s ? "Save" : "Add station", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(f)) return false;
        const d = App.formData(f);
        d.manager_ids = App.$$('[name="manager"]:checked', f).map((c) => Number(c.value));
        delete d.manager;
        if (s) await App.put(`/api/director/stations/${s.id}`, d); else await App.post("/api/director/stations", d);
        App.toast(s ? "Station updated." : `${d.name} added.`, "ok");
        load();
      },
    }] });
  }

  async function load() {
    [{ stations }, { managers }] = await Promise.all([App.get("/api/director/stations"), App.get("/api/director/managers")]);
    App.$("#list").innerHTML = App.table({
      columns: [
        { key: "name", label: "Station", render: (s) => `<strong>${App.esc(s.name)}</strong><div class="small muted">${App.esc(s.address || "")}</div>` },
        { key: "managers", label: "Managers", cls: "num" },
        { key: "staff", label: "Staff", cls: "num" },
        { key: "pumps", label: "Pumps", cls: "num" },
        { key: "is_active", label: "Status", render: (s) => s.is_active ? App.badge("ACTIVE", "Open") : App.badge("DISABLED", "Closed") },
        { key: "a", label: "", cls: "actions", render: (s) => `<button class="btn btn--sm" data-edit="${s.id}">Edit</button>
          <button class="btn btn--sm ${s.is_active ? "btn--danger" : "btn--ok"}" data-toggle="${s.id}">${s.is_active ? "Close" : "Re-open"}</button>` },
      ],
      rows: stations,
    });
  }

  App.$("#add-btn").addEventListener("click", () => open(null));
  App.on(page, "click", "[data-edit]", (e, b) => open(stations.find((s) => String(s.id) === b.dataset.edit)));
  App.on(page, "click", "[data-toggle]", (e, b) => App.run(async () => {
    const s = stations.find((x) => String(x.id) === b.dataset.toggle);
    if (s.is_active && !(await App.ask({ title: `Close ${App.esc(s.name)}?`, danger: true, confirmLabel: "Close station",
      message: "It will no longer appear for managers or in the totals. All its records are kept and you can re-open it." }))) return;
    await App.put(`/api/director/stations/${s.id}`, { is_active: !s.is_active });
    App.toast("Station updated.", "ok");
    load();
  }));

  await load();
});
