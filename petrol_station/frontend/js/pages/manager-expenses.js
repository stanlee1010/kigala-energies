/* EXPENSES — manager has full control; staff-entered expenses await approval */
App.run(async () => {
  await App.init({ role: "MANAGER", title: "Expenses" });
  const page = App.$("#page");
  const params = new URLSearchParams(location.search);
  let categories = [], rows = [];
  const categoriesReady = App.get("/api/expense-categories");   // fills the "Category" filter (starts on All)

  page.innerHTML = `
    <div class="page-head"><div><h1>Expenses</h1><p>Operating expenses reduce net profit. Only approved expenses are counted.</p></div>
      <div class="row"><button class="btn" id="cat-btn">+ Category</button><button class="btn btn--primary" id="add-btn">+ Add expense</button></div></div>
    <div class="card">
      <form id="filters" class="filters">
        <div class="field"><label>Status</label><select class="input" name="status">${App.options(["", "PENDING", "APPROVED", "REJECTED", "CANCELLED", "VOID"].map((s) => ({ v: s, l: s ? App.label(s) : "All" })), "v", "l", params.get("status") || "")}</select></div>
        <div class="field"><label>Category</label><select class="input" name="category_id">${App.options([], "id", "name", "", "All")}</select></div>
        <div class="field"><label>From</label><input class="input" type="date" name="start"></div>
        <div class="field"><label>To</label><input class="input" type="date" name="end"></div>
        <button class="btn" type="submit">Show</button>
      </form>
      <div id="list"></div>
    </div>`;

  function openForm(e) {
    const form = App.expenseForm(e, categories, { date: !(e && e.shift_id), reason: e && e.status === "APPROVED" });
    App.modal({ title: e ? "Edit expense" : "Add expense", body: form, actions: [{ label: "Cancel" }, {
      label: "Save", cls: "btn--primary",
      onClick: async () => {
        if (!Validation.validate(form)) return false;
        const data = await App.expenseData(form);
        if (e) await App.put(`/api/expenses/${e.id}`, data); else await App.post("/api/expenses", data);
        ({ categories } = await App.get("/api/expense-categories"));   // new words become suggestions
        App.toast("Expense saved.", "ok");
        load();
      },
    }] });
  }

  async function load() {
    const res = await App.get("/api/expenses" + App.qs(App.formData(App.$("#filters"))));
    rows = res.expenses;
    App.$("#list").innerHTML = App.table({
      columns: [
        { key: "expense_date", label: "Date", render: (r) => App.date(r.expense_date) },
        { key: "category", label: "Category" },
        { key: "description", label: "Description" },
        { key: "reference", label: "Reference", render: (r) => App.esc(r.reference || "—") },
        { key: "amount", label: "Amount", cls: "num", render: (r) => App.money(r.amount) },
        { key: "receipt", label: "Receipt", render: (r) => r.receipt_attachment_id ? `<a href="/api/attachments/${r.receipt_attachment_id}" target="_blank" rel="noopener">View</a>` : "—" },
        { key: "entered_by_name", label: "Entered by" },
        { key: "approved_by_name", label: "Approved by", render: (r) => App.esc(r.approved_by_name || "—") },
        { key: "status", label: "Status", render: (r) => App.badge(r.status) + (r.status_reason ? `<div class="small muted">${App.esc(r.status_reason)}</div>` : "") },
        { key: "a", label: "", cls: "actions", render: (r) => `
          ${["PENDING", "APPROVED"].includes(r.status) ? `<button class="btn btn--sm" data-edit="${r.id}">Edit</button>` : ""}
          ${r.status === "PENDING" ? `<button class="btn btn--sm btn--ok" data-act="approve" data-id="${r.id}">Approve</button><button class="btn btn--sm btn--danger" data-act="reject" data-id="${r.id}">Reject</button>` : ""}
          ${r.status === "APPROVED" ? `<button class="btn btn--sm btn--danger" data-act="void" data-id="${r.id}">Void</button>` : ""}` },
      ],
      rows, empty: "No expenses match.",
      footer: rows.length ? [{ html: "Approved total" }, {}, {}, {}, { cls: "num", html: App.money(res.approved_total) }, {}, {}, {}, {}, {}] : null,
    });
  }

  App.$("#filters").addEventListener("submit", (e) => { e.preventDefault(); App.run(load); });
  App.$("#add-btn").addEventListener("click", () => openForm(null));
  App.$("#cat-btn").addEventListener("click", () => App.run(async () => {
    const name = await App.ask({ title: "New expense category", confirmLabel: "Add", input: { label: "Category name", required: true } });
    if (!name) return;
    await App.post("/api/expense-categories", { name });
    ({ categories } = await App.get("/api/expense-categories"));
    App.toast("Category added.", "ok");
  }));
  App.on(page, "click", "[data-edit]", (e, b) => openForm(rows.find((r) => String(r.id) === b.dataset.edit)));
  App.on(page, "click", "[data-act]", (e, b) => App.run(async () => {
    const act = b.dataset.act;
    let reason = "";
    if (act !== "approve") {
      reason = await App.ask({ title: `${App.label(act)} expense`, confirmLabel: App.label(act), danger: true, input: { label: "Reason", required: true } });
      if (!reason) return;
    }
    await App.post(`/api/expenses/${b.dataset.id}/${act}`, { reason });
    App.toast("Expense updated.", "ok");
    load();
  }));

  await Promise.all([load(), categoriesReady.then((r) => {
    categories = r.categories;
    App.$('#filters [name="category_id"]').innerHTML = App.options(categories, "id", "name", "", "All");
  })]);
});
