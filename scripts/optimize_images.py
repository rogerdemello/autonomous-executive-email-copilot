"""Generate the WebP variants the landing page actually serves.

The screenshots are captured at 2x for sharpness, which makes them 2880px wide
and around half a megabyte each — served into a slot that is at most 1120 CSS
pixels. `landing.html` therefore uses `<picture>`: a WebP `srcset` at the
widths that are really displayed, with the original PNG as the fallback for
anything that cannot decode WebP.

This regenerates those variants. Run it after `capture_screenshots.py`, or
after replacing any image by hand — otherwise the page keeps serving the old
WebP and the new PNG is only ever seen by a browser that cannot read it, which
is the most confusing possible way for a screenshot update to appear to fail.

Requires Pillow (not a runtime dependency — this is a build-time tool)::

    pip install pillow && python scripts/optimize_images.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
IMG_DIR = _REPO_ROOT / "app" / "web" / "static" / "img"

# source -> widths to emit. The ceiling is 2x the largest slot the image is
# ever rendered into; anything above that is bytes nobody sees.
PLAN: dict[str, list[int]] = {
    "product-inbox.png": [1440, 2240],  # hero frame, ~1120 CSS px
    "product-spam.png": [1000, 1600],  # tour row, ~620 CSS px
    "product-approve.png": [1000, 1600],
    "office.jpg": [1200],
}

QUALITY = 82


def optimize(quality: int = QUALITY) -> list[Path]:
    from PIL import Image

    written: list[Path] = []
    for name, widths in PLAN.items():
        source = IMG_DIR / name
        if not source.exists():
            print(f"  skip {name}: not found")
            continue
        with Image.open(source) as image:
            # WebP has no palette/alpha story worth the trouble here, and every
            # one of these is an opaque screenshot.
            if image.mode in ("RGBA", "P", "LA"):
                image = image.convert("RGB")
            for width in widths:
                height = round(image.height * width / image.width)
                out = IMG_DIR / f"{source.stem}-{width}.webp"
                image.resize((width, height), Image.LANCZOS).save(
                    out, "WEBP", quality=quality, method=6
                )
                written.append(out)
                kb = round(out.stat().st_size / 1024)
                print(f"  {out.name:<28} {width}x{height}  {kb} KB")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--quality", type=int, default=QUALITY, help="WebP quality (default: %(default)s)."
    )
    args = parser.parse_args(argv)
    try:
        written = optimize(args.quality)
    except ImportError:
        print("Pillow is required: pip install pillow", file=sys.stderr)
        return 1
    print(f"\nWrote {len(written)} WebP variant(s) to {IMG_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
