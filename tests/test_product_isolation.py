"""Guard the import isolation of the gold-free product package.

`env/product/*` is the real-inbox runtime. To keep the deterministic benchmark
untouchable and the package reusable, it must NOT import the tenant/DB layer
(`env.saas`), the grader, or the simulator (`env.environment`). Mirrors
tests/test_connector_isolation.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PRODUCT_DIR = Path(__file__).resolve().parent.parent / "env" / "product"
_FORBIDDEN = ("env.saas", "env.grader", "env.environment")


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
