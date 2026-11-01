/* STATION SETTINGS — station details, timezone, currency, shift times, tolerances */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Station Settings" });
  const page = App.$("#page");
  const TIMEZONES = ["Africa/Dar_es_Salaam", "Africa/Nairobi", "Africa/Kampala", "Africa/Kigali", "Africa/Lusaka", "Africa/Johannesburg",
    "Africa/Lagos", "Africa/Cairo", "Europe/London", "UTC"];

  async function load() {
    const { station, settings } = await App.get("/api/settings");
    const zones = TIMEZONES.includes(settings.timezone) ? TIMEZONES : [settings.timezone, ...TIMEZONES];
    page.innerHTML = `
      <div class="page-head"><div><h1>Station settings</h1><p>Business logic uses the server clock in the station timezone below.</p></div></div>
      <form id="settings-form" class="stack" novalidate>
        <div class="card"><h2>Station</h2><div class="form-grid">
          <div class="field"><label>Station name</label><input class="input" name="name" value="${App.esc(station.name)}" required></div>
          <div class="field"><label>Address</label><input class="input" name="address" value="${App.esc(station.address || "")}"></div>
          <div class="field"><label>Phone</label><input class="input" name="phone" value="${App.esc(station.phone || "")}"></div>
          <div class="field"><label>Timezone</label><select class="input" data-s="timezone">${App.options(zones.map((z) => ({ z })), "z", "z", settings.timezone)}</select></div>
          <div class="field"><label>Currency</label><input class="input" data-s="currency" value="${App.esc(settings.currency)}" required></div>
          <div class="field"><label>System go-live date</label><input class="input" type="date" data-s="go_live_date" value="${App.esc(settings.go_live_date)}">
            <span class="hint">Shifts before this date are never reported as missing.</span></div>
        </div></div>
        <div class="card"><h2>Shift times</h2>
          <p>Shift names and times (for example Morning, Afternoon and Night), and who works which shift, are managed on the Shifts page.</p>
          <a class="btn" href="/manager/shifts.html">Open shift pattern &amp; roster</a></div>
        <div class="card"><h2>Warning tolerances</h2>
          <p class="small muted">Differences at or below these values do not raise a warning. Use 0 for strict checking.</p>
          <div class="form-grid">
            <div class="field"><label>Stock mismatch (litres)</label><input class="input" type="number" min="0" step="0.001" data-s="stock_mismatch_tolerance_litres" value="${settings.stock_mismatch_tolerance_litres}"></div>
            <div class="field"><label>Pump meter mismatch</label><input class="input" type="number" min="0" step="0.001" data-s="meter_mismatch_tolerance" value="${settings.meter_mismatch_tolerance}"></div>
            <div class="field"><label>Deposit difference (${App.esc(settings.currency)})</label><input class="input" type="number" min="0" step="0.01" data-s="deposit_tolerance" value="${settings.deposit_tolerance}"></div>
            <div class="field"><label>Refill-not-in-tank check (%)</label><input class="input" type="number" min="0" step="0.1" data-s="refill_dip_tolerance_pct" value="${settings.refill_dip_tolerance_pct}"></div>
          </div></div>
        <div><button class="btn btn--primary btn--lg" type="submit">Save settings</button></div>
      </form>`;

    const form = App.$("#settings-form");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!Validation.validate(form)) return;
      App.run(async () => {
        const d = App.formData(form);
        const s = {};
        App.$$("[data-s]", form).forEach((el) => { s[el.dataset.s] = el.value.trim(); });
        await App.put("/api/settings", { name: d.name, address: d.address, phone: d.phone, settings: s });
        App.forgetBoot();
        App.toast("Settings saved.", "ok");
        setTimeout(() => location.reload(), 600);
      });
    });
  }

  /* Which warnings are urgent: urgent ones are listed first, in red, and turn the menu counter red. */
  async function loadUrgency() {
    const { kinds } = await App.get("/api/alerts/urgency");
    const card = App.h(`<form class="card stack" id="urgency-form" novalidate>
      <h2>Which warnings are urgent</h2>
      <p class="small muted">Urgent warnings are shown first and in red, and the ⚠️ Warnings counter in the menu turns red while one
        is open. The others still appear, in amber. Changing a choice also changes warnings that are already open.</p>
      <div class="urgency-list">${kinds.map((k) => `
        <label class="urgency-item">
          <span><strong>${App.esc(k.name)}</strong><span class="small muted">${App.esc(k.about)}</span></span>
          ${k.always ? '<span class="badge badge--CRITICAL">Always urgent</span>'
            : `<span class="urgency-switch"><input type="checkbox" name="${App.esc(k.type)}" ${k.urgent ? "checked" : ""}> Urgent</span>`}
        </label>`).join("")}</div>
      <div><button class="btn btn--primary" type="submit">Save urgent warnings</button></div></form>`);
    page.appendChild(card);
    card.addEventListener("submit", (e) => {
      e.preventDefault();
      App.run(async () => {
        const urgent = {};
        App.$$('input[type="checkbox"]', card).forEach((c) => { urgent[c.name] = c.checked; });
        await App.put("/api/alerts/urgency", { urgent });
        App.toast("Urgent warnings saved.", "ok");
        App.refreshAlertCount();
      });
    });
  }

  await load();
  await loadUrgency();
});
