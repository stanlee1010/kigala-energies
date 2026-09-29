/* STAFF MANAGEMENT — create, edit, disable, reset password, view activity */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Staff Management" });
  const page = App.$("#page");

  page.innerHTML = `
    <div class="page-head"><div><h1>Staff management</h1><p>Every staff member logs in with their own account.</p></div>
      <button class="btn btn--primary" id="create-btn">+ Create staff</button></div>
    <div class="card" id="list"></div>`;

  let users = [], shiftTypes = [], stations = [], currentStation = null;

  function userForm(u = {}) {
    const isNew = !u.id;
    const regularId = u.default_shift_definition_id ?? "";
    const choices = [{ id: "", name: "— Not set —" }, ...shiftTypes.filter((t) => t.is_active || t.id === regularId)];
    const location = u.station_id || currentStation;
    const isSelf = u.id === App.user.id;
    const form = App.h(`<form novalidate><div class="form-grid">
      <div class="field"><label>Full name</label><input class="input" name="full_name" value="${App.esc(u.full_name || "")}" required></div>
      <div class="field"><label>Username</label><input class="input" name="username" value="${App.esc(u.username || "")}" ${isNew ? "required" : "disabled"} autocapitalize="none"></div>
      ${isNew ? `<div class="field"><label>Temporary password</label><input class="input" name="password" data-password required>
        <span class="hint">The staff member must change it at first login.</span></div>` : ""}
      <div class="field"><label>Phone</label><input class="input" name="phone" type="tel" value="${App.esc(u.phone || "")}"></div>
      <div class="field"><label>Email</label><input class="input" name="email" type="email" value="${App.esc(u.email || "")}"></div>
      ${isSelf ? "" : `<div class="field"><label>📍 Location (station)</label><select class="input" name="station_id" required>${App.options(stations, "id", "name", location)}</select>
        <span class="hint">The station where this person works.</span></div>`}
      ${isSelf ? "" : `<div class="field" data-regular><label>Regular shift</label><select class="input" name="shift_type_id">${App.options(choices, "id", "name", regularId)}</select>
        <span class="hint">Put on every upcoming shift of this type automatically.</span></div>
      <p class="small muted span-all hidden" data-other-station>To set this person's regular shift, switch to that station (top of the page) and edit them there.</p>`}
      ${isNew ? `<div class="field"><label>Status</label><select class="input" name="status"><option value="ACTIVE">Active</option><option value="DISABLED">Disabled</option></select></div>` : ""}
    </div></form>`);
    // Shift types belong to a station: the regular shift can only be chosen for the station open now.
    const loc = App.$('[name="station_id"]', form);
    const sync = () => {
      if (!loc) return;
      const here = Number(loc.value) === Number(currentStation);
      App.$("[data-regular]", form).classList.toggle("hidden", !here);
      App.$("[data-other-station]", form).classList.toggle("hidden", here);
    };
    if (loc) { loc.addEventListener("change", sync); sync(); }
    return form;
  }

  function openForm(u) {
    const form = userForm(u || {});
    App.modal({
      title: u ? `Edit ${App.esc(u.full_name)}` : "Create staff account", body: form,
      actions: [{ label: "Cancel" }, {
        label: u ? "Save changes" : "Create account", cls: "btn--primary",
        onClick: async () => {
          if (!Validation.validate(form)) return false;
          const data = App.formData(form);
          if (App.$("[data-regular]", form) && App.$("[data-regular]", form).classList.contains("hidden")) delete data.shift_type_id;
          if (u) await App.put(`/api/users/${u.id}`, data); else await App.post("/api/users", data);
          App.toast(u ? "Staff details updated." : `Account created for ${data.username}.`, "ok");
          load();
        },
      }],
    });
  }

  function resetPassword(u) {
    const form = App.h(`<form novalidate><p>Set a new temporary password for <strong>${App.esc(u.full_name)}</strong>. They must change it at next login.</p>
      <div class="field"><label>New temporary password</label><input class="input" name="password" data-password required></div></form>`);
    App.modal({ title: "Reset password", body: form, actions: [{ label: "Cancel" }, {
      label: "Reset password", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        await App.post(`/api/users/${u.id}/reset-password`, App.formData(form));
        App.toast("Password reset.", "ok");
      },
    }] });
  }

  async function activity(u) {
    const res = await App.get(`/api/users/${u.id}/activity`);
    App.modal({
      title: `Activity — ${App.esc(u.full_name)}`, wide: true,
      body: `<h3>Recent shifts</h3>${App.table({
        columns: [{ key: "label", label: "Shift" }, { key: "status", label: "Status", render: (r) => App.badge(r.status) }],
        rows: res.shifts, empty: "No shifts assigned yet." })}
        <h3 style="margin-top:1rem">Actions</h3>${App.table({
        columns: [
          { key: "created_at", label: "When", render: (r) => App.datetime(r.created_at) },
          { key: "action", label: "Action" },
          { key: "table_name", label: "Record", render: (r) => App.esc(`${r.table_name || ""} ${r.record_id ? "#" + r.record_id : ""}`) },
          { key: "reason", label: "Reason", render: (r) => App.esc(r.reason || "") },
        ], rows: res.activity, empty: "No activity recorded." })}`,
    });
  }

  async function load() {
    const [list, types] = await Promise.all([App.get("/api/users"), App.get("/api/shift-types")]);
    ({ users, stations, current_station_id: currentStation } = list);
    shiftTypes = types.shift_types;
    App.$("#list").innerHTML = App.table({
      columns: [
        { key: "full_name", label: "Staff", render: (u) => `<strong>${App.esc(u.full_name)}</strong>${u.role === "MANAGER" ? " " + App.badge("MANAGER", "Manager") : ""}` },
        { key: "username", label: "Username" },
        { key: "station_name", label: "Location", render: (u) => `📍 ${App.esc(u.station_name || "—")}` },
        { key: "phone", label: "Phone", render: (u) => App.esc(u.phone || "—") },
        { key: "is_active", label: "Status", render: (u) => u.is_active ? App.badge("ACTIVE", "Active") : App.badge("DISABLED", "Disabled") },
        { key: "regular_shift", label: "Regular shift", render: (u) => u.regular_shift ? App.esc(u.regular_shift) : '<span class="muted">Not set</span>' },
        { key: "assigned_shift", label: "Next shift", render: (u) => App.esc(u.assigned_shift || "—") },
        { key: "last_login_at", label: "Last login", render: (u) => App.datetime(u.last_login_at) },
        { key: "a", label: "Actions", cls: "actions", render: (u) => `
          <button class="btn btn--sm" data-act="edit" data-id="${u.id}">Edit</button>
          <button class="btn btn--sm" data-act="reset" data-id="${u.id}">Reset password</button>
          <button class="btn btn--sm" data-act="activity" data-id="${u.id}">Activity</button>
          ${u.id === App.user.id ? "" : u.is_active
            ? `<button class="btn btn--sm btn--danger" data-act="disable" data-id="${u.id}">Disable</button>`
            : `<button class="btn btn--sm btn--ok" data-act="enable" data-id="${u.id}">Enable</button>`}` },
      ],
      rows: users,
    });
  }

  App.$("#create-btn").addEventListener("click", () => openForm(null));
  App.on(page, "click", "[data-act]", (e, btn) => App.run(async () => {
    const u = users.find((x) => String(x.id) === btn.dataset.id);
    const act = btn.dataset.act;
    if (act === "edit") openForm(u);
    else if (act === "reset") resetPassword(u);
    else if (act === "activity") await activity(u);
    else if (act === "disable") {
      const reason = await App.ask({ title: "Disable account", message: `${App.esc(u.full_name)} will no longer be able to log in. Their records are kept.`, confirmLabel: "Disable", danger: true, input: { label: "Reason", required: false } });
      if (reason === false) return;
      await App.post(`/api/users/${u.id}/disable`, { reason });
      App.toast("Account disabled.", "ok"); load();
    } else if (act === "enable") {
      await App.post(`/api/users/${u.id}/enable`);
      App.toast("Account enabled.", "ok"); load();
    }
  }));

  await load();
});
