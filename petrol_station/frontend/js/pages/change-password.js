App.run(async () => {
  const user = await App.init({ title: "Change password" });
  const form = App.$("#pw-form");
  App.$("#first-login").classList.toggle("hidden", !user.must_change_password);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!Validation.validate(form)) return;
    const data = App.formData(form);
    try {
      await App.post("/api/auth/change-password", { current_password: data.current_password, new_password: data.new_password });
      App.toast("Password changed.", "ok");
      setTimeout(() => { location.href = App.home(user); }, 700);
    } catch (err) { App.error(err); }
  });
});
