"""CLI entrypoint to run the benchmark and write reproducible result artifacts.

Runs :class:`BenchmarkRunner` over configurable tasks, personas, seeds, and
agents, then writes ``results.json``, ``results.csv``, and ``results.html`` to an
output directory.

The deterministic ``baseline`` and ``multiagent`` agents run without any API key.
The ``llm`` agent requires ``OPENAI_API_KEY`` and is therefore opt-in: it is only
included when explicitly requested via ``--agents``.

Example::

    python scripts/run_benchmark.py --agents baseline multiagent --out reports/out
"""

from __future__ import annotations

import argparse
import os
import sys

# When invoked as ``python scripts/run_benchmark.py`` the script's own directory
# (not the repo root) lands on sys.path, so the ``benchmark`` package is not
# importable. Add the repo root explicitly to keep the entrypoint runnable from
# anywhere without installing the package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from benchmark.results_report import write_results_report  # noqa: E402
from benchmark.runner import (  # noqa: E402
    DEFAULT_PERSONAS,
    DEFAULT_SEEDS,
    DEFAULT_TASKS,
    BenchmarkResult,
    BenchmarkRunner,
)

# Agents that need no API key and run deterministically offline.
OFFLINE_AGENTS = ("baseline", "multiagent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the executive email benchmark and write result artifacts.",
    )
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=list(DEFAULT_TASKS),
        help="Task ids to run (default: all built-in tasks).",
    )
    parser.add_argument(
        "--personas",
        nargs="+",
        default=list(DEFAULT_PERSONAS),
        help="Personas to run (default: all built-in personas).",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=list(DEFAULT_SEEDS),
        help="Seeds to run (default: %(default)s).",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=("baseline", "multiagent", "llm"),
        default=list(OFFLINE_AGENTS),
        help=(
            "Agents to run (default: baseline multiagent). The 'llm' agent needs "
            "OPENAI_API_KEY and is only run when explicitly selected."
        ),
    )
    parser.add_argument(
        "--out",
        default="reports/benchmark",
        help="Output directory for results.json/.csv/.html (default: %(default)s).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=100,
        help="Maximum steps per episode (default: %(default)s).",
    )
    return parser


def run(
    tasks: list[str],
    personas: list[str],
    seeds: list[int],
    agents: list[str],
    out_dir: str,
    max_steps: int = 100,
) -> list[BenchmarkResult]:
    """Run the selected agents and write artifacts to ``out_dir``."""
    runner = BenchmarkRunner(
        tasks=tasks,
        personas=personas,
        seeds=seeds,
        max_steps=max_steps,
    )

    results: list[BenchmarkResult] = []
    for agent_name in agents:
        results.extend(runner.run_agent(agent_name))

    artifacts = write_results_report(results, out_dir)
    print(f"Wrote {len(results)} results across {len(agents)} agent(s) to {out_dir}")
    for name, path in artifacts.items():
        print(f"  {name}: {path}")

    return results


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run(
        tasks=args.tasks,
        personas=args.personas,
        seeds=args.seeds,
        agents=args.agents,
        out_dir=args.out,
        max_steps=args.max_steps,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
