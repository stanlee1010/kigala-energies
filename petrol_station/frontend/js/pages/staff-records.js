App.run(async () => {
  await App.init({ role: "STAFF", title: "My Records" });

  const showTab = (name) => {
    App.$$("#tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    App.$("#tab-refills").classList.toggle("hidden", name !== "refills");
    App.$("#tab-expenses").classList.toggle("hidden", name !== "expenses");
  };
  App.on(App.$("#tabs"), "click", ".tab", (e, tab) => showTab(tab.dataset.tab));
  if (new URLSearchParams(location.search).get("tab") === "expenses") showTab("expenses");

  async function loadRefills() {
    const { refills } = await App.get("/api/refills");
    App.$("#tab-refills").innerHTML = App.table({
      columns: [
        { key: "refill_date", label: "Date", render: (r) => `${App.date(r.refill_date)} ${String(r.refill_time).slice(0, 5)}` },
        { key: "fuel", label: "Fuel", render: (r) => App.fuelDot(r.fuel_color) + App.esc(r.fuel) },
        { key: "quantity_litres", label: "Quantity", cls: "num", render: (r) => App.litres(r.quantity_litres) },
        { key: "total_cost", label: "Total cost", cls: "num", render: (r) => App.money(r.total_cost) },
        { key: "status", label: "Status", render: (r) => App.badge(r.status) + (r.locked ? " 🔒" : "") },
        { key: "a", label: "", cls: "actions", render: (r) => r.locked ? "" : `<a class="btn btn--sm btn--warn" href="/staff/refill.html?edit=${r.id}">Finish / confirm</a>` },
      ],
      rows: refills, empty: "You have not recorded any refills yet.",
    });
  }

  // Expenses: the "Matumizi" sheet only — own words + amount per line, ➕ / −, total, edited in place.
  async function loadExpenses() {
    const [{ expenses, shift }, { categories }] = await Promise.all([
      App.get("/api/staff/expenses"), App.get("/api/expense-categories")]);
    App.expenseSheet(App.$("#tab-expenses"), {
      title: `Matumizi — ${shift ? `${shift} shift` : `today, ${App.date(App.serverToday())}`}`,
      items: expenses, editable: true, categories,
      save: async (lines) => {
        const res = await App.put("/api/staff/expenses", { lines });
        await loadExpenses();
        return res;
      },
    });
  }

  await Promise.all([loadRefills(), loadExpenses()]);
});
