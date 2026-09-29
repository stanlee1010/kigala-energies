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

  let categories = [];
  async function loadCategories() {
    ({ categories } = await App.get("/api/expense-categories"));
    App.$("#e-cat-list").innerHTML = categories.filter((c) => c.is_active).map((c) => `<option value="${App.esc(c.name)}">`).join("");
  }
  await loadCategories();
  let expenseRows = [];
  App.$("#e-date").value = App.serverToday();
  App.$("#e-date").max = App.serverToday();

  async function loadExpenses() {
    const { expenses } = await App.get("/api/expenses");
    expenseRows = expenses;
    App.$("#expense-list").innerHTML = "<h2>My expenses</h2>" + App.table({
      columns: [
        { key: "expense_date", label: "Date", render: (r) => App.date(r.expense_date) },
        { key: "category", label: "Category" },
        { key: "description", label: "Description" },
        { key: "amount", label: "Amount", cls: "num", render: (r) => App.money(r.amount) },
        { key: "status", label: "Status", render: (r) => App.badge(r.status) + (r.status_reason ? `<div class="small muted">${App.esc(r.status_reason)}</div>` : "") },
        { key: "a", label: "", cls: "actions", render: (r) => r.status === "PENDING"
          ? `<button class="btn btn--sm" data-edit="${r.id}">Edit</button> <button class="btn btn--sm" data-cancel="${r.id}">Cancel</button>` : "" },
      ],
      rows: expenses, empty: "No expenses recorded.",
    });
  }

  App.on(App.$("#expense-list"), "click", "[data-edit]", (e, btn) => {
    const expense = expenseRows.find((x) => String(x.id) === btn.dataset.edit);
    const form = App.expenseForm(expense, categories, { date: !expense.shift_id });
    App.modal({ title: "Edit expense", body: form, actions: [{ label: "Cancel" }, {
      label: "Save changes", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        await App.put(`/api/expenses/${expense.id}`, await App.expenseData(form));
        App.toast("Expense updated.", "ok");
        await Promise.all([loadExpenses(), loadCategories()]);
      },
    }] });
  });

  App.on(App.$("#expense-list"), "click", "[data-cancel]", (e, btn) => App.run(async () => {
    if (!(await App.ask({ title: "Cancel expense?", message: "The expense will be marked as cancelled.", confirmLabel: "Cancel expense", danger: true }))) return;
    await App.post(`/api/expenses/${btn.dataset.cancel}/cancel`);
    App.toast("Expense cancelled.", "ok");
    loadExpenses();
  }));

  const form = App.$("#expense-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!Validation.validate(form)) return;
    App.run(async () => {
      const data = App.formData(form);
      const file = App.$("#e-receipt").files[0];
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        const up = await App.api("POST", "/api/attachments", fd);
        data.receipt_attachment_id = up.attachment.id;
      }
      await App.post("/api/expenses", data);
      App.toast("Expense saved and sent for approval.", "ok");
      form.reset();
      App.$("#e-date").value = App.serverToday();
      loadExpenses();
      loadCategories();   // a new word typed as category is suggested from now on
    });
  });

  await Promise.all([loadRefills(), loadExpenses()]);
});
