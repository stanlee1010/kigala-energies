/* PUMP MANAGEMENT — add / edit / deactivate pumps, manage fuel types */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Pump Management" });
  const page = App.$("#page");
  page.innerHTML = `
    <div class="page-head"><div><h1>Pump management</h1><p>Add pumps as the station grows. Each pump is linked to one fuel type.</p></div>
      <div class="row"><button class="btn" id="fuel-btn">+ Fuel type</button><button class="btn btn--primary" id="add-btn">+ Add pump</button></div></div>
    <div class="card">
      <h2>Number of pumps</h2>
      <p class="small muted">Set how many pumps each fuel has. Removed pumps are switched off, not deleted, so their sales history is kept.</p>
      <div id="counts" class="grid-3"></div>
    </div>
    <div class="card" id="pumps"></div>
    <div class="card"><h2>Fuel types</h2><div id="fuels"></div></div>`;

  let pumps = [], fuels = [], tanks = [];

  function pumpForm(p = {}) {
    const fuelId = p.fuel_type_id || (fuels.find((f) => f.is_active) || {}).id;
    const nextNumber = p.pump_number || (Math.max(0, ...pumps.filter((x) => x.fuel_type_id === fuelId).map((x) => x.pump_number)) + 1);
    const form = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Fuel type</label><select class="input" name="fuel_type_id" required>${App.options(fuels.filter((f) => f.is_active || f.id === p.fuel_type_id), "id", "name", fuelId)}</select></div>
      <div class="field"><label>Pump number</label><input class="input" type="number" min="1" step="1" name="pump_number" value="${nextNumber}" required></div>
      <div class="field"><label>Draws from tank</label><select class="input" name="tank_id" required></select></div>
      <div class="field span-all"><label>Pump name</label><input class="input" name="pump_name" value="${App.esc(p.pump_name || "")}" placeholder="e.g. Petrol Pump 2">
        <span class="hint">Leave empty to use "&lt;Fuel&gt; Pump &lt;number&gt;".</span></div>
    </div></form>`);
    const fuelSel = App.$('[name="fuel_type_id"]', form);
    const syncTanks = () => {
      const list = tanks.filter((t) => String(t.fuel_type_id) === fuelSel.value);
      App.$('[name="tank_id"]', form).innerHTML = App.options(list, "id", "name", p.tank_id);
    };
    fuelSel.addEventListener("change", (e) => {
      syncTanks();
      // Suggest the next free number when the fuel changes on a new pump.
      if (!p.id) {
        const id = Number(e.target.value);
        App.$('[name="pump_number"]', form).value = Math.max(0, ...pumps.filter((x) => x.fuel_type_id === id).map((x) => x.pump_number)) + 1;
      }
    });
    syncTanks();
    return form;
  }

  function openPump(p) {
    const form = pumpForm(p || {});
    App.modal({ title: p ? `Edit ${App.esc(p.pump_name)}` : "Add pump", body: form, actions: [{ label: "Cancel" }, {
      label: p ? "Save" : "Add pump", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        const data = App.formData(form);
        if (p) await App.put(`/api/pumps/${p.id}`, data); else await App.post("/api/pumps", data);
        App.toast(p ? "Pump updated." : "Pump added.", "ok");
        load();
      },
    }] });
  }

  function openFuel(f) {
    const form = App.h(`<form novalidate><div class="form-grid">
      ${f ? "" : '<div class="field"><label>Code</label><input class="input" name="code" placeholder="e.g. KEROSENE" required></div>'}
      <div class="field"><label>Name</label><input class="input" name="name" value="${App.esc(f ? f.name : "")}" required></div>
      <div class="field"><label>Colour</label><input class="input" type="color" name="color" value="${App.esc(f ? f.color : "#64748b")}"></div>
      ${f ? `<label class="checkbox"><input type="checkbox" name="is_active" ${f.is_active ? "checked" : ""}> Active</label>` : ""}
    </div></form>`);
    App.modal({ title: f ? `Edit ${App.esc(f.name)}` : "Add fuel type", body: form, actions: [{ label: "Cancel" }, {
      label: "Save", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        const data = App.formData(form);
        if (f) await App.put(`/api/fuel-types/${f.id}`, data); else await App.post("/api/fuel-types", data);
        App.toast("Fuel type saved. Remember to set its price.", "ok");
        load();
      },
    }] });
  }

  async function load() {
    [{ pumps }, { fuel_types: fuels }, { tanks }] = await Promise.all([App.get("/api/pumps"), App.get("/api/fuel-types"), App.get("/api/tanks")]);
    App.$("#counts").innerHTML = fuels.filter((f) => f.is_active).map((f) => {
      const n = pumps.filter((p) => p.is_active && p.fuel_type_id === f.id).length;
      return `<form class="kpi" data-count="${f.id}" data-current="${n}" novalidate>
        <div class="kpi__label">${App.fuelDot(f.color)}${App.esc(f.name)} pumps</div>
        <div class="row" style="margin-top:.4rem">
          <button type="button" class="btn" data-step="-1" aria-label="One less ${App.esc(f.name)} pump">−</button>
          <input class="input input--big center" style="width:5.5rem" type="number" min="0" max="50" step="1" name="count" value="${n}" required>
          <button type="button" class="btn" data-step="1" aria-label="One more ${App.esc(f.name)} pump">+</button>
          <button type="submit" class="btn btn--primary">Save</button>
        </div></form>`;
    }).join("");
    const active = pumps.filter((p) => p.is_active).length;
    App.$("#pumps").innerHTML = `<div class="card__head"><h2>Pumps</h2><span class="muted">${active} active of ${pumps.length}</span></div>` + App.table({
      columns: [
        { key: "pump_name", label: "Pump", render: (p) => `${App.fuelDot(p.fuel_color)}<strong>${App.esc(p.pump_name)}</strong>` },
        { key: "fuel_type", label: "Fuel" },
        { key: "tank", label: "Tank", render: (p) => App.esc(p.tank || "—") },
        { key: "pump_number", label: "Number", cls: "num" },
        { key: "is_active", label: "Status", render: (p) => p.is_active ? App.badge("ACTIVE", "Active") : App.badge("DISABLED", "Inactive") },
        { key: "a", label: "", cls: "actions", render: (p) => `
          <button class="btn btn--sm" data-edit="${p.id}">Edit</button>
          ${p.is_active ? `<button class="btn btn--sm btn--danger" data-toggle="${p.id}" data-to="deactivate">Deactivate</button>`
                        : `<button class="btn btn--sm btn--ok" data-toggle="${p.id}" data-to="activate">Activate</button>`}` },
      ],
      rows: pumps, empty: "No pumps yet. Add the station's first pump.",
    });
    App.$("#fuels").innerHTML = App.table({
      columns: [
        { key: "name", label: "Fuel", render: (f) => App.fuelDot(f.color) + App.esc(f.name) },
        { key: "code", label: "Code" },
        { key: "is_active", label: "Status", render: (f) => f.is_active ? App.badge("ACTIVE", "Active") : App.badge("DISABLED", "Inactive") },
        { key: "a", label: "", cls: "actions", render: (f) => `<button class="btn btn--sm" data-fuel="${f.id}">Edit</button>` },
      ],
      rows: fuels,
    });
  }

  App.on(page, "click", "[data-step]", (e, b) => {
    const input = App.$('[name="count"]', b.closest("form"));
    input.value = Math.max(0, Math.min(50, (Number(input.value) || 0) + Number(b.dataset.step)));
  });
  App.on(page, "submit", "[data-count]", (e, form) => {
    e.preventDefault();
    if (!Validation.validate(form)) return;
    App.run(async () => {
      const fuel = fuels.find((f) => String(f.id) === form.dataset.count);
      const current = Number(form.dataset.current), wanted = Number(App.$('[name="count"]', form).value);
      if (wanted === current) { App.toast(`${fuel.name} already has ${current} pump(s).`); return; }
      if (wanted < current && !(await App.ask({
        title: `Reduce ${App.esc(fuel.name)} pumps to ${wanted}?`, danger: true, confirmLabel: "Reduce",
        message: `The ${current - wanted} highest-numbered ${App.esc(fuel.name)} pump(s) will be switched off. Their history is kept and you can switch them back on later.`,
      }))) return;
      const res = await App.put(`/api/pumps/count/${fuel.id}`, { count: wanted });
      const parts = [];
      if (res.added.length) parts.push(`added ${res.added.join(", ")}`);
      if (res.deactivated.length) parts.push(`switched off ${res.deactivated.join(", ")}`);
      App.toast(`${fuel.name}: ${res.active_pumps} pump(s) — ${parts.join("; ")}.`, "ok", 7000);
      load();
    });
  });

  App.$("#add-btn").addEventListener("click", () => openPump(null));
  App.$("#fuel-btn").addEventListener("click", () => openFuel(null));
  App.on(page, "click", "[data-edit]", (e, b) => openPump(pumps.find((p) => String(p.id) === b.dataset.edit)));
  App.on(page, "click", "[data-fuel]", (e, b) => openFuel(fuels.find((f) => String(f.id) === b.dataset.fuel)));
  App.on(page, "click", "[data-toggle]", (e, b) => App.run(async () => {
    const p = pumps.find((x) => String(x.id) === b.dataset.toggle);
    if (b.dataset.to === "deactivate" && !(await App.ask({ title: "Deactivate pump", message: `${App.esc(p.pump_name)} will no longer appear in new shifts. Its history is kept.`, confirmLabel: "Deactivate", danger: true }))) return;
    await App.post(`/api/pumps/${p.id}/${b.dataset.to}`);
    App.toast("Pump updated.", "ok");
    load();
  }));

  await load();
});
