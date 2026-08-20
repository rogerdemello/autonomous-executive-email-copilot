/* Progressive enhancement only.
 *
 * Every page works as plain HTML with forms — this file adds a theme toggle
 * and a confirmation on destructive actions. Nothing here is load-bearing, so
 * a JS error or a blocked script cannot break the demo.
 */
(function () {
  "use strict";

  // --- Theme toggle -------------------------------------------------------
  // base.html already applied the stored theme before paint; this only handles
  // the click and persists the choice.
  function currentTheme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit === "light" || explicit === "dark") return explicit;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try {
        localStorage.setItem("ec-theme", next);
      } catch (e) {
        /* private mode: the toggle still works for this page view */
      }
    });
  });

  // --- Confirm destructive submits ---------------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  });

  // --- Guard against double submits --------------------------------------
  // Approving twice would 409 on the second click; disabling the button after
  // the first submit keeps the demo clean without changing any behaviour.
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("button[type='submit']");
      if (!button) return;
      // Defer so the button's name/value still reaches the server.
      window.setTimeout(function () {
        button.disabled = true;
        button.textContent = "Working…";
      }, 0);
    });
  });
})();
