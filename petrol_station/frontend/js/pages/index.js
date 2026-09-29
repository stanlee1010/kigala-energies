/* Landing page: send the user to the right dashboard, or explain that the
   backend is missing (e.g. the page was opened with Live Server or file://). */
(async function () {
  "use strict";
  const noBackend = () => {
    document.getElementById("status").hidden = true;
    document.getElementById("no-backend").hidden = false;
  };
  if (location.protocol === "file:") return noBackend();
  try {
    const health = await fetch("/healthz", { headers: { Accept: "application/json" } });
    const data = await health.json();
    if (data.status !== "ok") return noBackend();
  } catch (e) {
    return noBackend();
  }
  App.api("GET", "/api/auth/me", undefined, { allow401: true })
    .then((me) => { location.replace(me.user.must_change_password ? "/change-password.html" : App.home(me.user)); })
    .catch(() => { location.replace("/login.html"); });
})();
