"""Guard the import isolation of the gold-free product package.

`env/product/*` is the real-inbox runtime. To keep the deterministic benchmark
untouchable and the package reusable, it must NOT import the tenant/DB layer
(`app.saas`), the grader, or the simulator (`research.sim.environment`). Mirrors
tests/test_connector_isolation.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PRODUCT_DIR = Path(__file__).resolve().parent.parent / "env" / "product"
_FORBIDDEN = ("app.saas", "research.sim.grader", "research.sim.environment")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_product_package_does_not_import_saas_grader_or_env():
    offenders: list[str] = []
    for py in _PRODUCT_DIR.rglob("*.py"):
        for module in _imported_modules(py):
            if any(module == f or module.startswith(f + ".") for f in _FORBIDDEN):
                offenders.append(f"{py.relative_to(_PRODUCT_DIR.parent.parent)} imports {module}")
    assert not offenders, "env/product must stay isolated:\n" + "\n".join(offenders)


def test_shared_policy_behavior_is_frozen_for_the_benchmark():
    """The product pipeline reuses ``BaselinePolicy`` + the classifier vocabulary,
    which the benchmark's golden snapshots also depend on. This pins the observable
    decision sequence on a fixed inbox so a real-inbox-driven tweak to the shared
    policy/classifier trips here (and the golden harness) instead of silently
    shifting benchmark results. If a change here is intentional, update this
    expectation in the same reviewed commit that regenerates the golden snapshots.
    """
    from app.copilot import enrich, pipeline
    from app.copilot.providers.fake import default_fixture_messages

    obs = enrich.to_observation(default_fixture_messages(), account_email="exec@acme.example")
    proposals = pipeline.to_proposals(pipeline.run_policy(obs))
    signature = [(p.action_type, p.email_id) for p in proposals]
    assert signature == [
        ("classify", "m-legal-1"),
        ("classify", "m-urgent-1"),
        ("classify", "m-spam-1"),
        ("classify", "m-normal-1"),
        ("escalate", "m-legal-1"),
        ("reply", "m-urgent-1"),
        ("defer", "m-normal-1"),
    ]
