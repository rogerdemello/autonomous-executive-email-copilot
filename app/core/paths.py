"""Project filesystem anchors, resolved once.

Every module that needs a path used to compute its own
``Path(__file__).parent.parent / ...``, which silently breaks the moment a
module changes directory depth. Anchoring here means a move can only ever break
one file.

Two different kinds of path live here, and conflating them is how this goes
wrong:

- **Package assets** (templates, stylesheets) ship *inside* the ``app`` package
  and are declared in ``[tool.setuptools.package-data]``. They are anchored to
  the package, so they resolve identically from a source checkout and from an
  installed wheel.
- **Project data** (``data/tasks.yaml``, scenarios, the demo mailbox) lives
  beside the package in the repository and is *not* packaged. It is anchored to
  the repository root, which means it is found when running from a checkout or
  from the container image (which copies the whole tree), but not from a
  non-editable ``pip install`` of the wheel alone. ``DATA_DIR`` overrides it for
  deployments that place the data elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

# app/core/paths.py -> app/core -> app
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# ... -> the repository root (the parent of the package).
PROJECT_ROOT = PACKAGE_ROOT.parent

# --- Project data (repo-relative, overridable) ------------------------------ #
DATA_ROOT = Path(os.environ.get("DATA_DIR") or (PROJECT_ROOT / "data")).resolve()
SCENARIOS_DIR = DATA_ROOT / "scenarios"
DEMO_DIR = DATA_ROOT / "demo"

# --- Package assets (shipped inside app/) ----------------------------------- #
WEB_DIR = PACKAGE_ROOT / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

__all__ = [
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "DATA_ROOT",
    "SCENARIOS_DIR",
    "DEMO_DIR",
    "WEB_DIR",
    "TEMPLATES_DIR",
    "STATIC_DIR",
]
