/* DIRECTOR — add or remove managers and choose which stations each one runs. */
App.run(async () => {
  await App.init({ role: "DIRECTOR", title: "Managers" });
  const page = App.$("#page");
  page.innerHTML = `
    <div class="page-head"><div><h1>Managers</h1>
      <p>Add managers, choose the stations each one runs, or remove their access. Their history is always kept.</p></div>
      <button class="btn btn--primary" id="add-btn">+ Add manager</button></div>
    <div class="card" id="list"></div>`;

  let managers = [], stations = [];

  function form(m) {
    const chosen = new Set(m ? m.station_ids : []);
    return App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Full name</label><input class="input" name="full_name" value="${App.esc(m ? m.full_name : "")}" required></div>
      <div class="field"><label>Username</label><input class="input" name="username" value="${App.esc(m ? m.username : "")}" ${m ? "disabled" : "required"} autocapitalize="none"></div>
      ${m ? "" : `<div class="field"><label>Temporary password</label><input class="input" name="password" data-password required>
        <span class="hint">They must change it at first login.</span></div>`}
      <div class="field"><label>Phone</label><input class="input" name="phone" value="${App.esc(m ? m.phone || "" : "")}"></div>
      <div class="field"><label>Email</label><input class="input" type="email" name="email" value="${App.esc(m ? m.email || "" : "")}"></div>
      <div class="field span-all"><label>Stations this manager runs</label>
        <div class="row">${stations.filter((s) => s.is_active || chosen.has(s.id)).map((s) => `
          <label class="checkbox"><input type="checkbox" name="station" value="${s.id}" ${chosen.has(s.id) ? "checked" : ""}> ${App.esc(s.name)}</label>`).join("")}</div>
        <span class="hint">Choose one or more. With two or more, the manager switches station at the top of the page.</span></div>
    </div></form>`);
  }

  function open(m) {
    const f = form(m);
    App.modal({ title: m ? `Edit ${App.esc(m.full_name)}` : "Add manager", body: f, actions: [{ label: "Cancel" }, {
      label: m ? "Save" : "Add manager", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(f)) return false;
        const d = App.formData(f);
        d.station_ids = App.$$('[name="station"]:checked', f).map((c) => Number(c.value));
        delete d.station;
        if (!d.station_ids.length) { App.toast("Choose at least one station", "error"); return false; }
        if (m) await App.put(`/api/director/managers/${m.id}`, d); else await App.post("/api/director/managers", d);
        App.toast(m ? "Manager updated." : `Manager ${d.username} added.`, "ok");
        load();
      },
    }] });
  }

  async function load() {
    ({ managers, stations } = await App.get("/api/director/managers"));
    App.$("#list").innerHTML = App.table({
      columns: [
        { key: "full_name", label: "Manager", render: (m) => `<strong>${App.esc(m.full_name)}</strong><div class="small muted">${App.esc(m.username)}</div>` },
        { key: "stations", label: "Stations", render: (m) => App.esc(m.stations.join(", ")) },
        { key: "phone", label: "Phone", render: (m) => App.esc(m.phone || "—") },
        { key: "is_active", label: "Access", render: (m) => (m.is_active ? App.badge("ACTIVE", "Active") : App.badge("DISABLED", "Removed"))
            + (m.two_step ? ' <span class="small" title="2-step login is on">🔐 2-step</span>' : "") },
        { key: "last_login_at", label: "Last login", render: (m) => App.datetime(m.last_login_at) },
        { key: "a", label: "", cls: "actions", render: (m) => `
          <button class="btn btn--sm" data-edit="${m.id}">Edit</button>
          <button class="btn btn--sm" data-reset="${m.id}">Reset password</button>
          ${m.two_step ? `<button class="btn btn--sm" data-reset-2step="${m.id}">Reset 2-step</button>` : ""}
          ${m.is_active ? `<button class="btn btn--sm btn--danger" data-toggle="${m.id}" data-to="disable">Remove</button>`
                        : `<button class="btn btn--sm btn--ok" data-toggle="${m.id}" data-to="enable">Give access back</button>`}` },
      ],
      rows: managers, empty: "No managers yet.",
    });
  }

  App.$("#add-btn").addEventListener("click", () => open(null));
  App.on(page, "click", "[data-edit]", (e, b) => open(managers.find((m) => String(m.id) === b.dataset.edit)));
  App.on(page, "click", "[data-reset]", (e, b) => {
    const m = managers.find((x) => String(x.id) === b.dataset.reset);
    const f = App.h(`<form novalidate><p>New temporary password for <strong>${App.esc(m.full_name)}</strong>. They must change it at next login.</p>
      <div class="field"><label>Temporary password</label><input class="input" name="password" data-password required></div></form>`);
    App.modal({ title: "Reset password", body: f, actions: [{ label: "Cancel" }, {
      label: "Reset", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(f)) return false;
        await App.post(`/api/director/managers/${m.id}/reset-password`, App.formData(f));
        App.toast("Password reset.", "ok");
      },
    }] });
  });
  App.on(page, "click", "[data-reset-2step]", (e, b) => App.run(async () => {
    const m = managers.find((x) => String(x.id) === b.dataset.reset2step);
    if (!(await App.ask({ title: "Reset 2-step login?", confirmLabel: "Reset 2-step", danger: true,
      message: `Only when <strong>${App.esc(m.full_name)}</strong> lost the phone with the authenticator app: their login asks for the password only until they turn 2-step login on again.` }))) return;
    await App.post(`/api/director/managers/${m.id}/reset-two-step`);
    App.toast("2-step login reset.", "ok");
    await load();
  }));
  App.on(page, "click", "[data-toggle]", (e, b) => App.run(async () => {
    const m = managers.find((x) => String(x.id) === b.dataset.toggle);
    if (b.dataset.to === "disable") {
      const reason = await App.ask({ title: `Remove ${App.esc(m.full_name)}?`, danger: true, confirmLabel: "Remove access",
        message: "They will no longer be able to log in. Everything they did stays in the records.",
        input: { label: "Reason (optional)", required: false } });
      if (reason === false) return;
      await App.post(`/api/director/managers/${m.id}/disable`, { reason });
    } else {
      await App.post(`/api/director/managers/${m.id}/enable`);
    }
    App.toast("Manager updated.", "ok");
    load();
  }));

  await load();
});
