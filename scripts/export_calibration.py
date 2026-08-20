"""Export the approval queue's (confidence, correct) pairs for calibration.

The drafter states a confidence with every model-written draft; reviewers
supply the ground truth by approving, editing, or rejecting it. This script
turns that corpus into the input `research/benchmark/calibration_cli.py`
already consumes — closing the loop between the product's live feedback and
the research repo's calibration math (Brier score, ECE):

    python scripts/export_calibration.py --org-slug northwind --out pairs.json
    python research/benchmark/calibration_cli.py pairs.json --verbose

"Correct" is strict: the draft went out exactly as written. An edited
approval counts as incorrect — the stated confidence did not survive contact
with a human reader.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org-slug", required=True, help="Workspace slug to export")
    parser.add_argument(
        "--out", type=Path, default=Path("calibration_pairs.json"), help="Output JSON path"
    )
    args = parser.parse_args(argv)

    from app.saas.learning import FeedbackService
    from app.saas.repository import OrganizationRepository

    org = OrganizationRepository().get_by_slug(args.org_slug)
    if org is None:
        print(f"No workspace with slug '{args.org_slug}'")
        return 1

    pairs = FeedbackService().calibration_pairs(org["id"])
    args.out.write_text(json.dumps(pairs, indent=2), encoding="utf-8")
    print(f"{len(pairs)} decided model drafts exported to {args.out}")
    if not pairs:
        print("Nothing to calibrate yet: pairs need model-written drafts a human has decided on.")
        return 0
    print(f"Next: python research/benchmark/calibration_cli.py {args.out} --verbose")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
