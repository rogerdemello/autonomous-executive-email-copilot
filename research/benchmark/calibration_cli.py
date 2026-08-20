from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

# Running a script puts its own directory on sys.path, not the repo root, so the
# first-party packages are not importable. Add the repo root explicitly to keep
# this entrypoint runnable directly (`python research/benchmark/calibration_cli.py`).
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research.sim.eval import brier_score, expected_calibration_error  # noqa: E402

Pair = tuple[float, bool]


def load_pairs(path: str) -> list[Pair]:
    with open(path) as f:
        data = json.load(f)
    pairs: list[Pair] = []
    for entry in data:
        confidence = entry.get("confidence")
        correct = entry.get("correct")
        if confidence is not None and correct is not None:
            pairs.append((float(confidence), bool(correct)))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibration CLI: compute Brier score and ECE from prediction pairs"
    )
    parser.add_argument("input", help="JSON file with [{confidence, correct}, ...]")
    parser.add_argument("--bins", type=int, default=10, help="Number of ECE bins (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Per-bin breakdown")

    args = parser.parse_args()
    pairs = load_pairs(args.input)

    if not pairs:
        print("No pairs found in input file", file=sys.stderr)
        sys.exit(1)

    brier = brier_score(pairs)
    ece = expected_calibration_error(pairs, n_bins=args.bins)
    n = len(pairs)
    n_correct = sum(1 for _, c in pairs if c)
    acc = n_correct / n
    avg_conf = mean(p for p, _ in pairs)

    print(f"Pairs:            {n}")
    print(f"Accuracy:         {acc:.4f}")
    print(f"Avg Confidence:   {avg_conf:.4f}")
    print(f"Brier Score:      {brier:.4f}  (0 = perfect)")
    print(f"ECE ({args.bins} bins):      {ece:.4f}  (0 = perfect)")


if __name__ == "__main__":
    main()
