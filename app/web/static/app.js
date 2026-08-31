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

  // --- Keyboard navigation -------------------------------------------------
  // The single biggest gap against Superhuman was that this app had no
  // keyboard surface at all beyond the theme toggle. Still progressive
  // enhancement: every one of these does something the mouse can already do,
  // so a blocked script costs speed and nothing else.
  //
  // Deliberately absent: a key that *sends*. `a` moves focus to Approve rather
  // than pressing it. Approving dispatches a real email to a real recipient,
  // and one mistyped character is not an acceptable way to trigger that.
  var list = document.querySelector("[data-msglist]");
  var searchBox = document.querySelector("[data-shortcut-search]");

  function isTyping(target) {
    if (!target) return false;
    var tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
  }

  function messageLinks() {
    return list ? Array.prototype.slice.call(list.querySelectorAll("[data-msg]")) : [];
  }

  function step(delta) {
    var links = messageLinks();
    if (!links.length) return;
    var current = links.findIndex(function (link) {
      return link.getAttribute("aria-current") === "true";
    });
    var next = current < 0 ? 0 : current + delta;
    if (next < 0 || next >= links.length) return;
    // A full navigation rather than client-side selection: the reader pane and
    // the copilot panel are server-rendered per message, so "select" and
    // "open" are the same act here.
    window.location.href = links[next].href;
  }

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (isTyping(event.target)) {
      // Escape gives the keyboard back without touching the mouse.
      if (event.key === "Escape") event.target.blur();
      return;
    }
    switch (event.key) {
      case "j":
        step(1);
        break;
      case "k":
        step(-1);
        break;
      case "/":
        if (searchBox) {
          event.preventDefault();
          searchBox.focus();
          searchBox.select();
        }
        break;
      case "e": {
        var draft = document.querySelector("[data-shortcut-draft]");
        if (draft) {
          event.preventDefault();
          draft.focus();
        }
        break;
      }
      case "a": {
        var approve = document.querySelector("[data-shortcut-approve]");
        if (approve) {
          event.preventDefault();
          approve.focus();
          approve.scrollIntoView({ block: "center" });
        }
        break;
      }
      default:
        break;
    }
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
