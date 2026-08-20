"""Grade the committed model drafts against the messages they answer.

The routing benchmark (`research/`) proves the policy picks the right action;
this grades the *prose*. The deterministic rubric (`app/llm/draft_eval.py`)
needs no key and no network, so it runs in CI on every push:

    python scripts/eval_drafts.py                # gate: exit 1 below --min-pass
    python scripts/eval_drafts.py --json out.json --min-pass 0.9

With a provider key, ``--judge`` adds an LLM-scored rubric (grounding, tone,
actionability, 1-5 each) per draft — the nightly trend, not the CI gate. The
flag degrades gracefully: no key means the judge section is skipped with a
note, and the exit code still reflects the deterministic gate.

Known, deliberate baseline: the committed demo corpus scores 10/11. The rubric
catches the model writing "by 25 September" for a message whose deadline is
30 September — kept as proof the gate catches real, subtle invention.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_JUDGE_PROMPT = """You are grading one email draft written on behalf of an executive.

SOURCE MESSAGE
Subject: {subject}
{source_body}
END SOURCE

DRAFT ({action_type})
{draft}
END DRAFT

Score 1-5 (5 best) and answer in JSON only:
{{"grounding": n, "tone": n, "actionability": n,
  "invented_facts": ["each claim not supported by the source"], "comment": "one sentence"}}
Grounding: every claim, name, number and date is supported by the source.
Tone: concise, professional, plausibly the executive's own voice.
Actionability: the recipient knows exactly what happens next."""


def _judge(results: list[dict], inbox_messages: list[dict]) -> list[dict]:
    """LLM-judge each graded draft; returns per-draft scores. Never raises."""
    try:
        from app.llm.parsing import extract_json_object
        from app.llm.providers import auto_detect_provider

        provider = auto_detect_provider()
    except Exception as exc:  # noqa: BLE001 - no key is the normal offline state
        print(f"  Judge skipped (no provider available: {exc})")
        return []

    from app.llm.draft_cache import draft_key

    drafts = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "demo" / "drafts.json").read_text(
            encoding="utf-8"
        )
    )["drafts"]
    by_id = {m["provider_message_id"]: m for m in inbox_messages}
    scores = []
    for row in results:
        message = by_id.get(row["provider_message_id"])
        if not message:
            continue
        key = draft_key(
            provider_message_id=message["provider_message_id"],
            subject=message.get("subject", ""),
            body=message.get("body", ""),
            action_type=row["action_type"],
        )
        draft = drafts.get(key, {}).get("body", "")
        prompt = _JUDGE_PROMPT.format(
            subject=message.get("subject", ""),
            source_body=message.get("body", ""),
            action_type=row["action_type"],
            draft=draft,
        )
        try:
            response = provider.generate(
                [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=300
            )
            parsed = extract_json_object(response.content or "") or {}
        except Exception as exc:  # noqa: BLE001 - one bad judgment must not kill the run
            parsed = {"error": str(exc)}
        scores.append(
            {
                "provider_message_id": row["provider_message_id"],
                "action_type": row["action_type"],
                **parsed,
            }
        )
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--min-pass",
        type=float,
        default=0.9,
        help="Fail (exit 1) when the deterministic pass rate drops below this (default 0.9)",
    )
    parser.add_argument("--json", type=Path, default=None, help="Write the full report here")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Also score each draft with an LLM rubric (needs a provider key; skipped without one)",
    )
    args = parser.parse_args(argv)

    from app.llm.draft_eval import evaluate_cache

    report = evaluate_cache()
    print(f"Draft quality: {report['passed']}/{report['drafts']} pass the deterministic rubric")
    for row in report["results"]:
        mark = "ok  " if row["passed"] else "FLAG"
        line = f"  {mark} {row['action_type']:<8} {row['provider_message_id']}"
        if not row["passed"]:
            details = "; ".join(
                f"{c['name']}: {c.get('detail', '')}".strip(": ")
                for c in row["checks"]
                if not c["passed"] and not c.get("soft")
            )
            line += f"  [{details}]"
        elif row.get("warnings"):
            line += f"  (warn: {', '.join(row['warnings'])})"
        print(line)
    if report["unmatched_cache_entries"]:
        print(
            f"  note: {report['unmatched_cache_entries']} cache entries match no current "
            "fixture message (source text was edited since generation)"
        )

    if args.judge:
        inbox = json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "demo" / "inbox.json").read_text(
                encoding="utf-8"
            )
        )["messages"]
        judge_scores = _judge(report["results"], inbox)
        report["judge"] = judge_scores
        if judge_scores:
            graded = [s for s in judge_scores if isinstance(s.get("grounding"), (int, float))]
            if graded:
                for dim in ("grounding", "tone", "actionability"):
                    mean = sum(s.get(dim, 0) for s in graded) / len(graded)
                    print(f"  judge {dim}: {mean:.2f}/5 over {len(graded)} drafts")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report written to {args.json}")

    rate = report["pass_rate"] or 0.0
    if rate < args.min_pass:
        print(f"FAIL: pass rate {rate} is below the {args.min_pass} gate")
        return 1
    print(f"PASS: pass rate {rate} meets the {args.min_pass} gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
