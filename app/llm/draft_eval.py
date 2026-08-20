"""Draft-quality rubric: the benchmark grades *routing*, this grades *prose*.

The gap it closes: `research/` proves the policy picks the right action, and
the approval queue proves a human liked the words once — but nothing between
releases catches the drafter quietly getting worse (inventing a deadline,
addressing the wrong person, drifting to five paragraphs). This module is that
regression gate.

Two layers, used together by ``scripts/eval_drafts.py``:

- **Deterministic checks** (this module, no network, runs in CI on every
  push): every number in a draft must appear in the source message; the
  greeting must name someone the source actually mentions; length bounds; no
  risky content; no assistant self-reference. These are blunt on purpose —
  each one catches a class of failure that has actually been observed, and a
  false positive is visible and arguable rather than silent.
- **LLM judge** (optional, needs a key): a rubric prompt scoring grounding,
  tone and actionability 1–5, for the nightly trend rather than the CI gate.

Honest baseline: the committed demo drafts score 10/11 — the rubric catches
the model writing "by 25 September" for a message whose deadline is
30 September. That flag is kept, not tuned away; it is the proof the gate
works.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.llm.safety.guardrails import detect_risky_content

# Bounds for executive prose: shorter is an empty gesture, longer is a memo.
MIN_CHARS = 40
MAX_CHARS = 2400

_NUMBER = re.compile(r"\d[\d,.:]*")
_AI_SELF_REFERENCE = re.compile(r"(?i)\b(as an ai|language model|i am an assistant|i'm an ai)\b")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    # Soft checks are reported but don't fail the draft: they flag patterns
    # that are *usually* wrong but have legitimate cases the rubric cannot
    # distinguish offline (e.g. the escalation target and the sender being the
    # same person).
    soft: bool = False


def _numbers(text: str) -> list[str]:
    """Digit groups, normalised: '£480k' → '480', '09:30.' → '09:30'."""
    return [n.strip(".,:") for n in _NUMBER.findall(text or "") if n.strip(".,:")]


def _leading_name(draft: str) -> str | None:
    """The addressee, if the draft opens with a one-word greeting ('Helena, …')."""
    first = (draft or "").split(",", 1)[0].strip()
    if first and len(first.split()) == 1 and first[:1].isupper() and first.isalpha():
        return first
    return None


def evaluate_draft(
    draft_body: str,
    *,
    subject: str = "",
    source_body: str = "",
    sender_name: str = "",
    sender: str = "",
    action_type: str = "reply",
) -> dict:
    """Run every deterministic check on one draft. Returns checks + verdict."""
    source = "\n".join([subject or "", source_body or "", sender_name or "", sender or ""])
    checks: list[CheckResult] = []

    length = len((draft_body or "").strip())
    checks.append(
        CheckResult(
            "length_within_bounds",
            MIN_CHARS <= length <= MAX_CHARS,
            f"{length} chars",
        )
    )

    # Grounding: a number the source never said is an invented fact — a wrong
    # amount or date in an executive's outbox is the costliest failure mode.
    source_numbers = set(_numbers(source))
    invented = [n for n in _numbers(draft_body) if n not in source_numbers]
    checks.append(
        CheckResult(
            "no_invented_numbers",
            not invented,
            f"not in source: {invented}" if invented else "",
        )
    )

    name = _leading_name(draft_body)
    checks.append(
        CheckResult(
            "greeting_is_grounded",
            name is None or name.lower() in source.lower(),
            f"addresses '{name}', who the source never mentions" if name else "",
        )
    )

    checks.append(CheckResult("no_risky_content", not detect_risky_content(draft_body)))
    checks.append(
        CheckResult("no_assistant_self_reference", not _AI_SELF_REFERENCE.search(draft_body or ""))
    )

    if action_type == "escalate":
        # A handover note briefs a colleague; opening with the sender's own
        # first name means the model answered the sender instead.
        sender_first = (sender_name or "").split(" ")[0].strip().lower()
        misaddressed = bool(name and sender_first and name.lower() == sender_first)
        checks.append(
            CheckResult(
                "handover_not_addressed_to_sender",
                not misaddressed,
                f"opens with the sender's name '{name}'" if misaddressed else "",
                # Soft: the escalation target can legitimately BE the sender
                # (outside counsel writes in; the handover goes back to them).
                soft=True,
            )
        )

    failed = [c.name for c in checks if not c.passed and not c.soft]
    warnings = [c.name for c in checks if not c.passed and c.soft]
    return {
        "passed": not failed,
        "flags": failed,
        "warnings": warnings,
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                **({"detail": c.detail} if c.detail else {}),
                **({"soft": True} if c.soft else {}),
            }
            for c in checks
        ],
    }


def evaluate_cache(inbox_path: Path | None = None, cache_path: Path | None = None) -> dict:
    """Grade every committed draft against the message it was written for.

    Matching goes through :func:`app.llm.draft_cache.draft_key`, so a draft
    whose source message has been edited simply stops matching (by design —
    the cache is content-addressed) and is reported as unmatched rather than
    graded against the wrong text.
    """
    from app.core.paths import DEMO_DIR
    from app.llm.draft_cache import draft_key

    inbox_file = inbox_path or DEMO_DIR / "inbox.json"
    cache_file = cache_path or DEMO_DIR / "drafts.json"
    messages = json.loads(inbox_file.read_text(encoding="utf-8"))["messages"]
    drafts = json.loads(cache_file.read_text(encoding="utf-8"))["drafts"]

    results = []
    matched_keys = set()
    for message in messages:
        for action_type in ("reply", "escalate"):
            key = draft_key(
                provider_message_id=message["provider_message_id"],
                subject=message.get("subject", ""),
                body=message.get("body", ""),
                action_type=action_type,
            )
            entry = drafts.get(key)
            if entry is None:
                continue
            matched_keys.add(key)
            verdict = evaluate_draft(
                entry.get("body", ""),
                subject=message.get("subject", ""),
                source_body=message.get("body", ""),
                sender_name=message.get("sender_name", ""),
                sender=message.get("sender", ""),
                action_type=action_type,
            )
            results.append(
                {
                    "provider_message_id": message["provider_message_id"],
                    "action_type": action_type,
                    "subject": message.get("subject", ""),
                    **verdict,
                }
            )

    unmatched = sorted(set(drafts) - matched_keys)
    passed = sum(1 for r in results if r["passed"])
    return {
        "drafts": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else None,
        "unmatched_cache_entries": len(unmatched),
        "results": results,
    }
