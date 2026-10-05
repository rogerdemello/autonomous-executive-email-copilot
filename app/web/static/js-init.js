// Stamps `data-js` on <html> before first paint, so the scroll-reveal styles
// (which hide sections until they scroll into view) only apply when scripting is
// actually running: a blocked or failed script leaves every section visible.
//
// Loaded WITHOUT defer, deliberately: it must run before the body renders. Lives
// in a file (not inline) so the CSP can stay at script-src 'self' with no inline
// allowance.
//
// (It used to be theme-init.js and applied a saved dark theme. There is one theme
// now, and it is light. The line below clears the choice an earlier visitor
// stored, so it does not linger in their browser forever.)
(function () {
  try {
    localStorage.removeItem("ec-theme");
  } catch (e) {
    /* private mode: nothing was stored, nothing to clear */
  }
  document.documentElement.setAttribute("data-js", "");
})();
