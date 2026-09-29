/* ==========================================================================
   Client-side validation. The server re-validates everything; this only
   gives staff immediate feedback while typing at the pump.
   ========================================================================== */
(function () {
  "use strict";

  const Validation = (window.Validation = {});

  function fieldBox(input) {
    return input.closest(".field") || input.parentElement;
  }

  Validation.setError = function (input, message) {
    const box = fieldBox(input);
    let err = box.querySelector(".error");
    if (message) {
      input.classList.add("is-invalid");
      input.setAttribute("aria-invalid", "true");
      if (!err) { err = document.createElement("div"); err.className = "error"; box.appendChild(err); }
      err.textContent = message;
    } else {
      input.classList.remove("is-invalid");
      input.removeAttribute("aria-invalid");
      if (err) err.remove();
    }
  };

  Validation.clear = function (form) {
    form.querySelectorAll(".is-invalid").forEach((el) => Validation.setError(el, null));
  };

  function labelOf(input) {
    const box = fieldBox(input);
    const label = box && box.querySelector("label");
    return label ? label.textContent.replace(/\(.*\)/, "").trim() : input.name;
  }

  /** Validate required/number/min/positive/password rules declared in HTML attributes. */
  Validation.validate = function (form) {
    let firstBad = null;
    form.querySelectorAll("input, select, textarea").forEach((input) => {
      if (input.disabled || input.type === "hidden" || input.closest(".hidden")) return;
      const value = (input.value || "").trim();
      let message = null;
      if (input.required && !value) message = `${labelOf(input)} is required`;
      else if (value && input.type === "number") {
        const n = Number(value);
        if (!Number.isFinite(n)) message = `${labelOf(input)} must be a number`;
        else if (input.min !== "" && n < Number(input.min)) message = `${labelOf(input)} cannot be less than ${input.min}`;
        else if (input.dataset.positive !== undefined && n <= 0) message = `${labelOf(input)} must be greater than zero`;
      } else if (value && input.dataset.password !== undefined) {
        if (value.length < 8) message = "Password must be at least 8 characters";
        else if (/^\d+$/.test(value) || /^[A-Za-z]+$/.test(value)) message = "Use both letters and numbers";
      } else if (value && input.dataset.match) {
        const other = form.querySelector(`[name="${input.dataset.match}"]`);
        if (other && other.value !== input.value) message = "Passwords do not match";
      }
      Validation.setError(input, message);
      if (message && !firstBad) firstBad = input;
    });
    if (firstBad) firstBad.focus();
    return !firstBad;
  };

  /**
   * Live meter check: Fuel Sold = Closing - Opening.
   * Returns { ok, sold } and marks the closing input invalid when closing < opening.
   */
  Validation.meter = function (openingInput, closingInput) {
    closingInput.dataset.liveValidate = "1";
    const o = Number(openingInput.value), c = Number(closingInput.value);
    if (closingInput.value === "" || openingInput.value === "" || !Number.isFinite(o) || !Number.isFinite(c)) {
      Validation.setError(closingInput, null);
      return { ok: true, sold: null };
    }
    if (c < o) {
      Validation.setError(closingInput, "⚠️ INVALID METER READING — Closing meter reading cannot be lower than opening meter reading.");
      return { ok: false, sold: null };
    }
    Validation.setError(closingInput, null);
    return { ok: true, sold: c - o };
  };

  /** Live-clear errors as the user types (fields with their own live check manage themselves). */
  document.addEventListener("input", (e) => {
    if (e.target.dataset && e.target.dataset.liveValidate) return;
    if (e.target.classList && e.target.classList.contains("is-invalid") && e.target.value.trim()) {
      Validation.setError(e.target, null);
    }
  });
})();
