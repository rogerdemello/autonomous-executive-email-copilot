"""No page may scroll sideways at 320px, in either theme.

WCAG 1.4.10 puts the floor at 320 CSS pixels. This is checked with a real
browser because the failure is a *computed layout* property: the CSS that
caused it looked completely correct in the file. The signed-in app rendered
620px wide inside a 320px viewport for one reason — an `fr` grid track and an
`overflow-x: auto` nav both floor at min-content unless told otherwise, so the
sidebar's widest row set the width of the entire application. No amount of
reading the stylesheet finds that; measuring it does, immediately.

Skipped unless Playwright is installed, like ``scripts/capture_screenshots.py``
— the tool this borrows its browser from. CI runs without it, so this is a
check the person changing the CSS runs, not a gate that blocks a deploy on a
missing browser binary.

To run: ``pip install playwright && playwright install chromium``
"""

from __future__ import annotations

import socket
import threading
import time
import uuid

import pytest

pytest.importorskip("playwright", reason="Playwright is optional; see this module's docstring")

from playwright.sync_api import sync_playwright  # noqa: E402

# WCAG 1.4.10 Reflow: content must not require horizontal scrolling at a width
# equivalent to 320 CSS pixels.
VIEWPORT_WIDTH = 320

PUBLIC_PAGES = [
    "/",
    "/login",
    "/signup",
    "/forgot-password",
    "/privacy",
    "/terms",
    "/contact-sales",
]
APP_PAGES = [
    "/app/inbox",
    "/app/approvals",
    "/app/waiting",
    "/app/activity",
    "/app/settings",
    "/app/connect",
]

# Elements inside a scroll or clip container are *supposed* to exceed the
# viewport — that is the container doing its job. Only unclipped boxes that
# widen the document are failures.
_CULPRITS_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const clipped = el => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p);
      if (['hidden', 'auto', 'scroll', 'clip'].includes(o.overflowX)) return true;
    }
    return false;
  };
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1 && !clipped(el)) {
      const parent = el.parentElement;
      const pr = parent ? parent.getBoundingClientRect() : null;
      // Report the outermost offender, not every descendant it drags along.
      if (!pr || pr.right <= vw + 1) {
        const cls = (el.className || '').toString().split(' ').filter(Boolean).slice(0, 2).join('.');
        out.push(`${el.tagName.toLowerCase()}${cls ? '.' + cls : ''} right=${Math.round(r.right)}`);
      }
    }
  });
  return out.slice(0, 8);
}"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server() -> str:
    """A real uvicorn server — a browser cannot talk to a TestClient."""
    import uvicorn

    from app.core.db import migrate_db
    from app.main import app

    migrate_db()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:
            pytest.fail("the test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def _sign_up_and_connect(page, base: str) -> None:
    page.goto(f"{base}/signup")
    page.fill("#org_name", "Northwind Industries")
    page.fill("#full_name", "Alex Chen")
    page.fill("#email", f"reflow_{uuid.uuid4().hex[:10]}@northwind.example")
    page.fill("#password", "a-strong-password")
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    page.goto(f"{base}/app/connect")
    page.click("form[action='/app/connect/demo'] button[type=submit]")
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_no_page_scrolls_sideways_at_320px(live_server, theme):
    failures = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": 800}, color_scheme=theme
        )
        page = context.new_page()
        _sign_up_and_connect(page, live_server)

        for path in PUBLIC_PAGES + APP_PAGES:
            page.goto(live_server + path)
            page.wait_for_load_state("networkidle")
            document_width = page.evaluate("document.documentElement.scrollWidth")
            viewport_width = page.evaluate("document.documentElement.clientWidth")
            if document_width > viewport_width + 1:
                failures.append(
                    f"{path} [{theme}]: document is {document_width}px wide in a "
                    f"{viewport_width}px viewport — {page.evaluate(_CULPRITS_JS)}"
                )
        browser.close()

    assert not failures, "horizontal scrolling at 320px:\n  " + "\n  ".join(failures)
