"""Re-capture the product screenshots the landing page renders.

The landing page frames three screenshots under the heading "Real product.
Real inbox. No mockups." That claim decays the moment the product changes and
the images do not, so capturing them has to be a command rather than an
afternoon.

This boots the real app against a throwaway data directory, seeds the demo
workspace, signs in as the demo owner, and photographs the actual pages —
nothing is composited or mocked. The dev database is never touched: the
fixtures are copied to a temp tree and ``DATA_DIR`` points there, exactly as
``tests/conftest.py`` does.

Requires Playwright and a Chromium build::

    pip install playwright && python -m playwright install chromium
    python scripts/capture_screenshots.py

Then regenerate the WebP variants the template actually serves::

    python scripts/optimize_images.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

OUT_DIR = _REPO_ROOT / "app" / "web" / "static" / "img"

# (filename, path, viewport, description). Widths match the `width`/`height`
# attributes in landing.html, so the rendered aspect ratio is exactly what the
# page reserves space for and nothing reflows on load.
SHOTS = [
    ("product-inbox.png", "/app/inbox", (1440, 900), "the triaged inbox"),
    ("product-spam.png", "/app/inbox?label=spam", (1000, 625), "a message classified as spam"),
    # Tall enough to include the editable draft and the Approve / Reject
    # buttons: "edit it before it sends" is the whole claim this shot makes.
    ("product-approve.png", "/app/approvals", (1000, 720), "the approval queue"),
    # The product's whole argument in one frame: the model wrote "advise by 25
    # September" for a message whose deadline is the 30th, and the verifier caught
    # it, showing the sentence and the source line it failed against. Used by the
    # README, not the landing page.
    ("product-flagged.png", "/app/approvals", (1000, 720), "the verifier catching an invention"),
    # The picture a link unfurls to on LinkedIn, Slack and WhatsApp (og:image).
    # 1200x630 is the box every one of them crops to; JPEG because not all of
    # them read WebP; and kept small because WhatsApp drops previews whose image
    # is much over 300 KB.
    ("og-card.jpg", "/app/inbox", (1200, 630), "the link-preview card"),
]


# Shots that photograph one element rather than the viewport. The flagged draft
# is the 3rd-ish card down the approvals page, so a viewport shot of the page
# would miss it.
ELEMENT_SHOTS = {"product-flagged.png": ".action-block:has(.chip--warn)"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _isolated_data_dir() -> str:
    """A copy of the repo's data fixtures, minus the databases."""
    tmp = Path(tempfile.mkdtemp(prefix="eec-shots-"))
    shutil.copytree(
        _REPO_ROOT / "data",
        tmp,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.db", "*.db-journal", "*.sqlite", "*.sqlite3"),
    )
    return str(tmp)


def _wait_for(url: str, timeout: float = 30.0) -> None:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"the app did not come up at {url}")


def capture(scale: int = 2, only: list[str] | None = None) -> list[Path]:
    from playwright.sync_api import sync_playwright

    from app.saas.demo_seed import DEMO_OWNER_EMAIL, DEMO_OWNER_PASSWORD, seed_demo

    summary = seed_demo(fresh=True)
    print(
        f"Seeded the demo workspace: {summary['messages']} messages, "
        f"{summary['pending']} held for approval"
    )

    import uvicorn

    port = _free_port()
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    shots = [shot for shot in SHOTS if not only or shot[0] in only]
    if not shots:
        raise SystemExit(f"--only matched nothing; choose from: {', '.join(n for n, *_ in SHOTS)}")

    base = f"http://127.0.0.1:{port}"
    written: list[Path] = []
    try:
        _wait_for(f"{base}/health")
        OUT_DIR.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as play:
            browser = play.chromium.launch()
            context = browser.new_context(
                viewport={"width": shots[0][2][0], "height": shots[0][2][1]},
                device_scale_factor=scale,
                color_scheme="light",
                # Freeze the animation layer. Scroll reveals start elements at
                # opacity 0, which is how you photograph a blank page.
                reduced_motion="reduce",
            )
            page = context.new_page()

            page.goto(f"{base}/login")
            page.fill("#email", DEMO_OWNER_EMAIL)
            page.fill("#password", DEMO_OWNER_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(f"{base}/app/**")

            for name, path, (width, height), what in shots:
                page.set_viewport_size({"width": width, "height": height})
                page.goto(f"{base}{path}")
                page.wait_for_load_state("networkidle")
                # Blur whatever autofocused, so no screenshot ships a focus ring.
                page.evaluate("document.activeElement && document.activeElement.blur()")
                target = OUT_DIR / name
                if name.endswith(".jpg"):
                    # scale="css": exactly width x height whatever the device
                    # pixel ratio is, since a link preview is not retina.
                    page.screenshot(path=str(target), type="jpeg", quality=82, scale="css")
                    print(
                        f"  {name}  {width}x{height}  {what}  ({target.stat().st_size // 1024} KB)"
                    )
                elif name in ELEMENT_SHOTS:
                    card = page.locator(ELEMENT_SHOTS[name]).first
                    card.wait_for()
                    card.screenshot(path=str(target))
                    print(f"  {name}  {what}  (element: {ELEMENT_SHOTS[name]})")
                else:
                    page.screenshot(path=str(target))
                    print(f"  {name}  {width}x{height}@{scale}x  {what}")
                written.append(target)

            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--scale", type=int, default=2, help="Device pixel ratio (default: %(default)s)."
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="FILE",
        help="Capture just these files (e.g. og-card.jpg) instead of all of them.",
    )
    args = parser.parse_args(argv)

    # Set before importing anything that resolves DATA_ROOT or builds the engine.
    os.environ["DATA_DIR"] = _isolated_data_dir()
    os.environ.setdefault("ENVIRONMENT", "development")
    os.environ.setdefault("DEMO_LOGIN_ENABLED", "true")

    written = capture(scale=args.scale, only=args.only)
    print(f"\nWrote {len(written)} screenshot(s) to {OUT_DIR}")
    print("Now regenerate the WebP variants: python scripts/optimize_images.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
