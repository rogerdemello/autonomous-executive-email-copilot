// Apply the saved theme before first paint so a dark-mode user never sees a
// white flash on navigation. Loaded WITHOUT defer, deliberately: it must run
// before the body renders. Lives in a file (not inline) so the CSP can stay
// at script-src 'self' with no inline allowance.
(function () {
  try {
    var t = localStorage.getItem("ec-theme");
    if (t) document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    /* private mode: fall back to prefers-color-scheme */
  }
  // Stamp that scripting is live. Scroll-reveal styles are gated on this
  // attribute so a blocked script leaves every section fully visible.
  document.documentElement.setAttribute("data-js", "");
})();
