"""Project filesystem anchors, resolved once.

Every module that needs a repo-relative path used to compute its own
``Path(__file__).parent.parent / ...``, which silently breaks the moment a
module changes directory depth. Anchoring here means a move can only ever break
one file.
"""

from __future__ import annotations

from pathlib import Path

# app/core/paths.py -> app/core -> app -> <repo root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_ROOT = PROJECT_ROOT / "data"
SCENARIOS_DIR = DATA_ROOT / "scenarios"
DEMO_DIR = DATA_ROOT / "demo"

WEB_DIR = PROJECT_ROOT / "app" / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

__all__ = [
    "PROJECT_ROOT",
    "DATA_ROOT",
    "SCENARIOS_DIR",
    "DEMO_DIR",
    "WEB_DIR",
    "TEMPLATES_DIR",
    "STATIC_DIR",
]
