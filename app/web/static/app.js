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

  // --- Scroll reveals ------------------------------------------------------
  // Elements marked data-reveal fade/rise in as they enter the viewport. The
  // hidden initial state only applies under html[data-js] (set by
  // theme-init.js), so nothing can be stranded invisible if this file fails.
  var revealables = document.querySelectorAll("[data-reveal]");
  if (revealables.length) {
    if ("IntersectionObserver" in window) {
      var observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-in");
              observer.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.12, rootMargin: "0px 0px -36px 0px" }
      );
      revealables.forEach(function (el) {
        observer.observe(el);
      });
    } else {
      revealables.forEach(function (el) {
        el.classList.add("is-in");
      });
    }
  }

  // --- Sticky header shadow ------------------------------------------------
  var siteHead = document.querySelector(".site-head");
  if (siteHead) {
    var onScroll = function () {
      siteHead.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

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
