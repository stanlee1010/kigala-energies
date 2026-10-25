(function () {
  "use strict";
  const form = App.$("#login-form");

  // Already logged in? Go straight to the right dashboard.
  App.api("GET", "/api/auth/me", undefined, { allow401: true })
    .then((me) => { location.href = App.home(me.user); })
    .catch(() => null);

  async function login(credentials, url) {
    const { user } = await App.api("POST", url || "/api/auth/login", credentials, { allow401: true });
    // Saved copies of pages' data belong to whoever used this device before: forget them.
    if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage("clear-data");
    const next = new URLSearchParams(location.search).get("next");
    if (user.must_change_password) location.href = "/change-password.html";
    else location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : App.home(user);
  }

  // After too many wrong passwords the server says how long to wait: count it down on the button.
  const btn = App.$("button[type=submit]", form);
  let timer = null;
  function waitThenRetry(seconds) {
    clearInterval(timer);
    const end = Date.now() + seconds * 1000;
    const tick = () => {
      const left = Math.ceil((end - Date.now()) / 1000);
      if (left <= 0) { clearInterval(timer); btn.disabled = false; btn.textContent = "Log in"; return; }
      btn.disabled = true;
      btn.textContent = `Wait ${left >= 60 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : `${left} s`}`;
    };
    tick();
    timer = setInterval(tick, 1000);
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!Validation.validate(form) || btn.disabled) return;
    btn.disabled = true;
    try {
      await login(App.formData(form));
    } catch (err) {
      App.error(err);
      App.$("#password").value = "";
      App.$("#password").focus();
      if (err.retryAfter) { waitThenRetry(err.retryAfter); return; }
    }
    btn.disabled = false;
  });

  // Training portal: show the practice accounts. Real portal: link to training if it is running.
  App.api("GET", "/api/demo/info", undefined, { allow401: true }).then((info) => {
    if (info.demo) {
      document.body.classList.add("is-training");
      App.$("#station-name").textContent = "🎓 Training portal";
      App.$("#login-hint").textContent = "Tap the shift you want to practise. You can practise the day or the night shift at any time.";
      App.$("#forgot").textContent = "Practice only — nothing here affects the real station.";
      form.classList.add("hidden");   // trainees choose a shift; the server signs them in (no password)
      App.$("#demo-panel").classList.remove("hidden");
      App.$("#demo-accounts").innerHTML = info.accounts.map((a) => `
        <button type="button" class="demo-account" data-user="${App.esc(a.username)}">
          <span style="font-size:1.6rem">${a.night ? "🌙" : "☀️"}</span>
          <span><strong>${App.esc(a.shift || "Practice")} shift${a.start ? ` · ${App.esc(a.start)}–${App.esc(a.end)}` : ""}</strong>
            <small>${App.esc(a.full_name)} · ${App.esc(a.username)}${a.people > 1 ? ` · ${a.people} people, own pumps` : ""}</small></span>
        </button>`).join("");
      App.on(App.$("#demo-accounts"), "click", "[data-user]", (e, b) => {
        login({ username: b.dataset.user }, "/api/demo/login").catch(App.error);
      });
    } else {
      if (info.training_available !== false) showTrainingLink(info.training_url, info.training_running);
    }
  }).catch(() => showTrainingLink(null, false));

  // Real portal: always offer the training portal under "Forgot your password?".
  function showTrainingLink(url, running) {
    const box = App.$("#training-link");
    App.$("a", box).href = url || `${location.protocol}//${location.hostname}:8001/`;
    App.$("#training-note").textContent = running === false
      ? "If it does not open, ask the manager to start the training portal (start-training.bat)."
      : "";
    box.classList.remove("hidden");
  }
})();
