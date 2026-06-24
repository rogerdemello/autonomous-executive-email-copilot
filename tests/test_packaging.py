"""Packaging completeness: every source subpackage ships and imports.

Each expected package must have an ``__init__.py`` (so setuptools find-packages
includes it in a wheel) and must import cleanly. Dependency-free so it runs in any
environment.
"""

from __future__ import annotations

import importlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_EXPECTED = [
    "env",
    "env.agents",
    "env.learning",
    "env.safety",
    "env.eval",
    "env.connectors",
    "baseline",
    "server",
    "benchmark",
    "telemetry",
    "reports",
]


def test_all_subpackages_have_init_and_import() -> None:
    for pkg in _EXPECTED:
        init = _ROOT.joinpath(*pkg.split(".")) / "__init__.py"
        assert init.is_file(), f"{pkg} missing __init__.py — a wheel would drop it"
        importlib.import_module(pkg)
