"""Isolation guarantees for the read-only email connector (INV-2 / INV-5 defense).

These statically prove the connector can never affect grading or determinism:
- the grader and environment do not import the connector package;
- the connector package does not import the grader or environment;
- the benchmark refuses to run while a connector is enabled.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

_ENV = Path(__file__).resolve().parents[1] / "env"


def _src(rel: str) -> str:
    return (_ENV / rel).read_text(encoding="utf-8")


def _imported_module_leaves(rel: str) -> set[str]:
    """Return the final dotted segment of every module imported by ``rel``."""
    tree = ast.parse(_src(rel))
    leaves: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                leaves.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom) and node.module:
            leaves.add(node.module.split(".")[-1])
    return leaves


def test_grader_and_environment_do_not_import_connectors() -> None:
    # AST-based: a docstring mention is fine; an actual import is not.
    for module in ("grader.py", "environment.py"):
        assert "connectors" not in _imported_module_leaves(module)


def test_connectors_do_not_import_grader_or_environment() -> None:
    for f in ("__init__.py", "base.py", "mapping.py", "imap_readonly.py", "config.py"):
        leaves = _imported_module_leaves(f"connectors/{f}")
        assert "grader" not in leaves, f"connectors/{f} imports grader"
        assert "environment" not in leaves, f"connectors/{f} imports environment"


def test_benchmark_refuses_to_run_with_connector_enabled(monkeypatch) -> None:
    monkeypatch.setenv("EMAIL_CONNECTOR_ENABLED", "true")

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_benchmark", script)
    assert spec and spec.loader
    run_benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_benchmark)

    raised = False
    try:
        run_benchmark.run(
            tasks=["easy_classification"],
            personas=["balanced"],
            seeds=[42],
            agents=["baseline"],
            out_dir="artifacts/should_not_be_written",
        )
    except RuntimeError as exc:
        raised = True
        assert "EMAIL_CONNECTOR_ENABLED" in str(exc)
    assert raised, "benchmark must refuse to run with the connector enabled"
