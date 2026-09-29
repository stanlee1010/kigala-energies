/* ==========================================================================
   Training guide — step-by-step lessons for pump attendants (staff).
   "Try it" buttons open the matching page in the training portal.
   Ticks are saved on this device only (a per-person convenience).
   ========================================================================== */
(function () {
  "use strict";

  const LESSONS = [
    { title: "Log in", page: "/login.html",
      steps: ["Open the training portal and tap the practice account for <strong>your shift</strong> (there is one attendant per shift).",
              "In the real system you type the username and temporary password the manager gave you, then choose your own password."],
      expect: "You land on <strong>My Shift</strong>, showing your own shift." },
    { title: "Start your shift", page: "/staff/index.html",
      steps: ["On <strong>My Shift</strong>, check the shift name, date, times and your pumps.",
              "Press <strong>▶ START MY SHIFT</strong>."],
      expect: "The status changes to <em>Open</em> and a box appears for every pump the manager has set up." },
    { title: "Enter the opening meter readings", page: "/staff/index.html",
      steps: ["Under <strong>1. Pump meter readings</strong>, compare with <em>Previous closing meter</em>.",
              "Type the opening meter for each pump and press <strong>Save readings</strong>."],
      expect: "Each pump shows <em>Opening saved</em>. If a number differs from the previous closing meter, the manager is told." },
    { title: "Record fuel delivered into a tank (and confirm it)", page: "/staff/refill.html",
      steps: ["Open <strong>Fuel Refill</strong>. Choose the fuel. If the fuel has more than one tank, choose the tank the fuel went into.",
              "Type <strong>how many litres were added to the tank</strong>, the supplier, invoice number and cost per litre.",
              "Check that <em>Total cost</em> = litres × cost per litre, then press <strong>Continue to confirmation</strong>.",
              "<strong>Practise:</strong> press <strong>NO — GO BACK AND CORRECT</strong>, change the litres, and continue again.",
              "When everything is right press <strong>YES — CONFIRM</strong>."],
      expect: "<strong>✅ Fuel refill successfully confirmed.</strong> The record is locked for you — only the manager can correct it. The system adds the litres to the tank automatically; you do not need to measure or type the tank stock." },
    { title: "Enter the closing readings", page: "/staff/index.html",
      steps: ["At the end of the shift type each pump's closing meter. Fuel sold and revenue appear as you type.",
              "<strong>Practise:</strong> type a closing number lower than the opening number."],
      expect: "<strong>⚠️ INVALID METER READING</strong> appears and the reading cannot be saved. Fix the number and press <strong>Save readings</strong>. The litres sold are taken out of the pump's tank automatically." },
    { title: "Check the money and submit the shift", page: "/staff/index.html",
      steps: ["At the top, <strong>Money expected from this shift</strong> shows what you should hand in (Petrol + Diesel revenue).",
              "Press <strong>✔ SUBMIT MY SHIFT</strong> and confirm."],
      expect: "The shift shows <em>Submitted</em>. You can no longer change it; the manager reviews it." },
    { title: "Record an expense with a receipt", page: "/staff/records.html",
      steps: ["Open <strong>My Records → My expenses</strong>.",
              "Fill in the date, category, amount and description, add a photo of the receipt, and press <strong>Save expense</strong>."],
      expect: "It shows <em>Pending</em> until the manager approves it. You can cancel it while it is pending." },
  ];

  const KEY = "training-progress-v2";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } };
  const save = (v) => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } };
  let done = load();
  let base = "";          // prefix for "Try it" links (training portal address)
  let canTry = true;

  function render() {
    const finished = LESSONS.filter((_, i) => done[i]).length;
    App.$("#progress").textContent = `${finished} of ${LESSONS.length} lessons done`;
    App.$("#lessons").innerHTML = LESSONS.map((l, i) => `
      <article class="card lesson ${done[i] ? "lesson--done" : ""}">
        <div class="lesson__head">
          <span class="lesson__num">${i + 1}</span>
          <h2>${l.title}</h2>
          <label class="checkbox no-print"><input type="checkbox" data-done="${i}" ${done[i] ? "checked" : ""}> Done</label>
        </div>
        <ol>${l.steps.map((s) => `<li>${s}</li>`).join("")}</ol>
        <p class="lesson__expect"><strong>What you should see:</strong> ${l.expect}</p>
        ${canTry ? `<a class="btn btn--primary no-print" href="${base}${l.page}" target="_blank" rel="noopener">Try it →</a>` : ""}
      </article>`).join("");
  }

  App.on(App.$("#lessons"), "change", "[data-done]", (e, box) => {
    done[box.dataset.done] = box.checked;
    save(done);
    render();
  });
  App.$("#print-btn").addEventListener("click", () => window.print());
  App.$("#clear-btn").addEventListener("click", () => { done = {}; save(done); render(); });

  // Where do the "Try it" buttons go? Training portal itself, or the running training portal.
  App.api("GET", "/api/demo/info", undefined, { allow401: true }).then((info) => {
    const where = App.$("#where");
    if (info.demo) {
      where.innerHTML = '<div class="banner__icon">🎓</div><div class="banner__body"><h2>You are in the training portal</h2><p>Use the practice account for your shift — password <strong>Demo1234</strong>. Use <em>Reset training data</em> in the yellow bar to start fresh.</p></div>';
    } else if (info.training_url) {
      base = info.training_url.replace(/\/$/, "");
      where.innerHTML = `<div class="banner__icon">🎓</div><div class="banner__body"><h2>Practise in the training portal</h2><p>"Try it" opens the training portal at <a href="${App.esc(info.training_url)}">${App.esc(info.training_url)}</a> — not the real station.</p></div>`;
    } else {
      canTry = false;
      where.innerHTML = '<div class="banner__icon">ℹ️</div><div class="banner__body"><h2>The training portal is not running</h2><p>Ask the manager to start it (start-training.bat), then reload this page to get the "Try it" buttons.</p></div>';
    }
    where.classList.remove("hidden");
    render();
  }).catch(render);
  render();
})();
