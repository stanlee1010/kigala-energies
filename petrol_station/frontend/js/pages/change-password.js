App.run(async () => {
  const user = await App.init({ title: "Change password" });
  const form = App.$("#pw-form");
  App.$("#first-login").classList.toggle("hidden", !user.must_change_password);
  if (!user.must_change_password) App.$('label[for="current_password"]').textContent = "Current password";
  const leader = ["MANAGER", "DIRECTOR"].includes(user.role);
  App.$("#new_password + .hint").textContent = leader
    ? "At least 10 characters, with letters and numbers. Not an easy password and not your username."
    : "At least 8 characters, with letters and numbers. Not an easy password and not your username.";

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!Validation.validate(form)) return;
    const data = App.formData(form);
    try {
      await App.post("/api/auth/change-password", { current_password: data.current_password, new_password: data.new_password });
      App.forgetBoot();
      App.toast("Password changed.", "ok");
      setTimeout(() => { location.href = App.home(user); }, 700);
    } catch (err) { App.error(err); }
  });

  /* 2-step login (managers and the director; optional): a 6-digit code from an authenticator app
     after the password, so a stolen password alone is not enough. */
  if (!leader || App.demo || user.must_change_password) return;
  const card = App.h('<div class="card stack" style="max-width:520px" id="two-step"></div>');
  App.$(".content").appendChild(card);

  async function show() {
    const { enabled } = await App.get("/api/auth/two-step");
    card.innerHTML = enabled
      ? `<h2>🔐 2-step login is on</h2>
         <p>After your password you type the 6-digit code from your authenticator app.</p>
         <p class="small muted">Lost the phone? The director can switch it off for a manager; for the director, ask the person
           who runs the station computer.</p>
         <button type="button" class="btn" data-off>Turn off 2-step login</button>`
      : `<h2>🔐 2-step login</h2>
         <p>Protect this account with a code from an authenticator app (Google Authenticator, Microsoft Authenticator…)
           on your phone. Someone who learns your password still cannot log in without your phone.</p>
         <button type="button" class="btn btn--primary" data-on>Turn on 2-step login</button>`;
  }

  App.on(card, "click", "[data-on]", () => App.run(async () => {
    const password = await App.ask({ title: "Turn on 2-step login", confirmLabel: "Continue",
      message: "Type your password to continue.", input: { label: "Password", required: true, type: "password" } });
    if (!password) return;
    const s = await App.post("/api/auth/two-step/setup", { password });
    const qr = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(s.qr_svg)))}`;
    card.innerHTML = `<h2>🔐 Turn on 2-step login</h2>
      <ol class="stack">
        <li>Open your authenticator app and add an account by <strong>scanning this code</strong>:
          <div class="center" style="margin:.75rem 0"><img src="${qr}" alt="QR code for the authenticator app" width="220" height="220"></div>
          On this phone? <a href="${App.esc(s.link)}">Open in the authenticator app</a>. Or type this key:
          <div><code style="font-size:1.05rem;letter-spacing:.08em;word-break:break-all">${App.esc(s.secret.replace(/(.{4})/g, "$1 ").trim())}</code></div></li>
        <li>Type the 6-digit code the app shows:</li>
      </ol>
      <form class="stack" novalidate data-confirm>
        <input class="input input--big" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required aria-label="6-digit code">
        <div class="row"><button class="btn btn--primary" type="submit">Turn on</button>
          <button class="btn btn--ghost" type="button" data-cancel>Cancel</button></div>
      </form>`;
    App.$('[name="code"]', card).focus();
  }));

  App.on(card, "submit", "[data-confirm]", (e, f) => App.run(async () => {
    e.preventDefault();
    await App.post("/api/auth/two-step/confirm", { code: App.$('[name="code"]', f).value });
    App.forgetBoot();
    App.toast("2-step login is on. From now on, keep your phone with you to log in.", "ok", 7000);
    await show();
  }));
  App.on(card, "click", "[data-cancel]", () => App.run(show));

  App.on(card, "click", "[data-off]", () => {
    const f = App.h(`<form class="stack" novalidate>
      <div class="field"><label>Password</label><input class="input" type="password" name="password" autocomplete="current-password" required></div>
      <div class="field"><label>6-digit code from the app</label><input class="input" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required></div></form>`);
    App.modal({ title: "Turn off 2-step login", body: f, actions: [{ label: "Cancel" }, {
      label: "Turn off", cls: "btn--danger",
      onClick: async () => {
        await App.post("/api/auth/two-step/off", App.formData(f));
        App.forgetBoot();
        App.toast("2-step login is off.", "ok");
        await show();
      },
    }] });
  });

  await show();
});
