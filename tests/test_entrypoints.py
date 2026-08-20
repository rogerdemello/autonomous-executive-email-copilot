"""Every documented CLI must actually be runnable.

`python path/to/script.py` puts the *script's* directory on ``sys.path``, not the
repo root, so a first-party import fails with ModuleNotFoundError. Nothing in the
test suite catches that, because pytest imports modules rather than running them
— which is exactly how several of these sat broken while the suite stayed green.

These tests execute each entrypoint in a subprocess the way a human or CI would.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Entrypoints a person is told to run — by the README, the docs, the Makefile,
# or CI. Each must survive being invoked as a plain script path.
ENTRYPOINTS = [
    "scripts/seed_demo.py",
    "scripts/run_benchmark.py",
    "scripts/regen_golden.py",
    "scripts/issue_license.py",
    "scripts/contamination_check.py",
    "scripts/eval_drafts.py",
    "scripts/export_calibration.py",
    "research/inference.py",
    "research/baseline/run_baseline.py",
    "research/benchmark/calibration_cli.py",
    "research/benchmark/ab_eval.py",
]


@pytest.mark.parametrize("script", ENTRYPOINTS)
def test_entrypoint_is_runnable_as_a_script(script: str) -> None:
    path = REPO_ROOT / script
    assert path.is_file(), f"{script} is referenced but missing"

    result = subprocess.run(  # noqa: S603 - fixed, in-repo argv
        [sys.executable, str(path), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=REPO_ROOT,
    )
    combined = result.stdout + result.stderr
    assert "ModuleNotFoundError" not in combined, (
        f"{script} cannot import its own package when run as a script. "
        f"It needs the repo-root sys.path guard.\n{combined[-800:]}"
    )
    assert "Traceback" not in combined, f"{script} failed to start:\n{combined[-800:]}"


def test_the_app_module_exposes_both_entrypoints() -> None:
    """`uvicorn app.main:app` and the `server` console script must both resolve."""
    from app.main import app, main

    assert app is not None
    assert callable(main)


def test_inference_still_emits_the_log_contract() -> None:
    """CI parses these markers; the format is a contract, not incidental output."""
    result = subprocess.run(  # noqa: S603 - fixed, in-repo argv
        [
            sys.executable,
            str(REPO_ROOT / "research/inference.py"),
            "--task",
            "easy_classification",
            "--max-steps",
            "5",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=REPO_ROOT,
    )
    combined = result.stdout + result.stderr
    assert "[START]" in combined
    assert "[STEP]" in combined
    assert "[END]" in combined
