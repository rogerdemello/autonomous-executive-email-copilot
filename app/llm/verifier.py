"""Draft-then-verify: check a draft against its source before it queues.

The drafter writes; this module reads back. Before a reply or handover enters
the approval queue, its prose is checked against the message it answers:

- **Deterministic layer** (always on, no network): the same rubric CI runs —
  numbers must appear in the source, greetings must name someone the source
  mentions, no risky content, no assistant self-reference. Free, so every
  held action gets at least this.
- **Model layer** (only when live drafting is enabled): a second, cheap pass
  that lists claims the source does not support. It runs with the *drafter's*
  provider already configured — no extra setup — and any failure degrades to
  the deterministic verdict alone.

What comes back is not just a verdict. Each finding carries the **sentence in
the draft** it is about and, where one exists, the **line in the source** it
was checked against. A reviewer told "unsupported claim: the 25th" has to go
hunting; a reviewer shown the sentence, the source line beside it, and a
button that removes the sentence has a decision in front of them. That
difference is the product — every competitor ships triage and voice-matched
drafting, and none of them can tell you whether the draft it wrote is true.

The verdict is stored on the action (``verification_status``,
``verification_notes``, ``verification_claims``). Nothing here blocks an
action: a flagged draft still queues, because the human is the gate.
Verification tells them where to look first.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from app.copilot.providers.base import FetchedMessage

logger = logging.getLogger(__name__)

VERIFIED = "verified"
FLAGGED = "flagged"

# Sentence splitting good enough to quote back at a human. Deliberately not a
# parser: it only has to find the fragment the reviewer's eye should land on.
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_NUMBER = re.compile(r"\d[\d,.:/-]*")


@dataclass(frozen=True)
class Finding:
    """One thing worth looking at, with the evidence for looking at it.

    ``claim`` is the fragment of the *draft*. ``source`` is the line of the
    *source message* it was checked against, or None when the point is that
    the source says nothing of the kind — which is itself the finding.
    """

    kind: str
    detail: str
    claim: str = ""
    source: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Verdict:
    status: str
    notes: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    # What the model check cost, when one was made. Carried out rather than
    # recorded here for the same reason the drafter carries its usage out: this
    # module has no business knowing which tenant to bill, and giving it one
    # would make it another place that could get it wrong.
    cost_usd: float = 0.0
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def flagged(self) -> bool:
        return self.status == FLAGGED

    def __iter__(self):
        """Unpack as ``(status, notes)``.

        Kept because that two-tuple was this function's contract before
        findings existed, and it is a perfectly good way to ask the narrow
        question "was it flagged, and roughly why".
        """
        return iter((self.status, self.notes))


_VERIFY_PROMPT = """You are fact-checking one email draft against the message it answers.

SOURCE MESSAGE
Subject: {subject}
{source_body}
END SOURCE

DRAFT
{draft}
END DRAFT

List every specific claim in the DRAFT (amounts, dates, names, commitments,
stated facts) that the SOURCE does not support. Style and tone are not claims.

For each one, quote the exact sentence from the DRAFT that makes the claim, and
quote the line of the SOURCE you checked it against — or use null for "source"
when the source says nothing on the subject at all.

Answer in JSON only:
{{"unsupported": [{{"claim": "<sentence from the draft>",
                   "source": "<line from the source, or null>",
                   "why": "<one short clause>"}}]}}
Use an empty list if everything is supported."""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s.strip()]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _sentence_containing(draft: str, needle: str) -> str:
    """The draft sentence a fragment appears in, or the fragment itself."""
    for sentence in _sentences(draft):
        if needle and needle in sentence:
            return sentence
    return needle


def _source_line_mentioning(source: str, needle: str) -> str | None:
    """The source line that mentions ``needle``, if any."""
    for line in _lines(source):
        if needle and needle in line:
            return line
    return None


def _nearest_numeric_line(source: str) -> str | None:
    """A source line that carries numbers, as the place to check a figure.

    When a draft cites a number the source never states, the useful evidence
    is not "nothing matched" but "here is what the source *did* say with
    numbers in it" — which is where the reviewer's eye needs to go.
    """
    for line in _lines(source):
        if _NUMBER.search(line):
            return line
    return None


def _deterministic_findings(draft_body: str, verdict: dict, *, source_text: str) -> list[Finding]:
    """Turn the rubric's failed checks into findings a reviewer can act on."""
    findings: list[Finding] = []
    for check in verdict["checks"]:
        if check["passed"] or check.get("soft"):
            continue
        name = check["name"]
        detail = check.get("detail", "")

        if name == "no_invented_numbers":
            # detail is "not in source: ['25', '480']" — recover the figures so
            # each one gets its own finding pointing at its own sentence.
            for number in re.findall(r"'([^']+)'", detail):
                findings.append(
                    Finding(
                        kind="invented_number",
                        detail=f"The source never states {number}.",
                        claim=_sentence_containing(draft_body, number),
                        source=_nearest_numeric_line(source_text),
                    )
                )
            continue

        if name == "greeting_is_grounded":
            greeting = _sentences(draft_body)[0] if _sentences(draft_body) else ""
            findings.append(
                Finding(
                    kind="ungrounded_greeting",
                    detail=detail or "The greeting names someone the source never mentions.",
                    claim=greeting,
                    source=None,
                )
            )
            continue

        findings.append(
            Finding(
                kind=name,
                detail=detail or name.replace("_", " "),
                claim="",
                source=None,
            )
        )
    return findings


@dataclass(frozen=True)
class _ModelCheck:
    """What the model check found, and what it cost."""

    findings: list[Finding]
    cost_usd: float = 0.0
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _model_findings(draft_body: str, message: FetchedMessage) -> _ModelCheck | None:
    """The model layer. Returns None when unavailable — never raises."""
    try:
        from app.llm.parsing import extract_json_object
        from app.llm.providers import auto_detect_provider, calculate_cost

        provider = auto_detect_provider()
        response = provider.generate(
            [
                {
                    "role": "user",
                    "content": _VERIFY_PROMPT.format(
                        subject=message.subject or "",
                        source_body=(message.body or "")[:4000],
                        draft=draft_body,
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=600,
        )
        # getattr, not attribute access: the contract of this layer is that it
        # never costs verification anything. A provider that returns a response
        # without usage metadata should cost the *ledger* a row, not cost the
        # reviewer the findings.
        model = getattr(response, "model", "") or ""
        usage = getattr(response, "usage", None)
        cost = calculate_cost(model, usage) if usage else 0.0

        parsed = extract_json_object(response.content or "")
        if parsed is None or not isinstance(parsed.get("unsupported"), list):
            # The call was still made and still billed. Report the cost with no
            # findings rather than dropping it — spend that only lands in the
            # ledger when the model answers usefully is spend that hides.
            return _ModelCheck(
                findings=[],
                cost_usd=cost,
                model=model,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            )

        source_text = f"{message.subject or ''}\n{message.body or ''}"
        findings: list[Finding] = []
        for item in parsed["unsupported"][:6]:
            # Tolerate the older shape (a bare string) as well as the object
            # form: a model that ignores half the instruction should degrade to
            # a weaker finding, not to no verification at all.
            if isinstance(item, str):
                claim, source, why = item.strip(), None, ""
            elif isinstance(item, dict):
                claim = str(item.get("claim") or "").strip()
                raw_source = item.get("source")
                source = str(raw_source).strip() if raw_source else None
                why = str(item.get("why") or "").strip()
            else:
                continue
            if not claim:
                continue
            # Trust the model for the judgement, not for the quoting: keep its
            # source line only when it really is in the source.
            if source and source not in source_text:
                source = _source_line_mentioning(source_text, source[:40]) or None
            findings.append(
                Finding(
                    kind="unsupported_claim",
                    detail=why or "The source does not support this.",
                    claim=_sentence_containing(draft_body, claim) or claim,
                    source=source,
                )
            )
        return _ModelCheck(
            findings=findings,
            cost_usd=cost,
            model=model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
    except Exception as exc:  # noqa: BLE001 - verification must never break a sync
        logger.info("Model verification unavailable: %s", exc)
        return None


def verify_draft(
    draft_body: str,
    *,
    message: FetchedMessage,
    action_type: str,
    live_llm: bool = False,
    budget=None,
) -> Verdict:
    """Verify one draft.

    Returns a :class:`Verdict`, which also unpacks as ``(status, notes)``.
    Soft rubric warnings do not flag on their own — they exist for the eval
    report, not the reviewer.

    ``budget`` is an :class:`~app.saas.llm_budget.LlmBudget` when the caller
    knows whose bill this is. Verification is the *second* paid call a held
    action can make — one to write the draft, one to check it — so it is
    metered on the same allowance. Out of budget drops to the deterministic
    checks, which is where every deployment without an API key already lives:
    claims are still checked against the source text, just not by a model.
    """
    from app.llm.draft_eval import evaluate_draft

    verdict = evaluate_draft(
        draft_body,
        subject=message.subject or "",
        source_body=message.body or "",
        sender_name=message.sender_name or "",
        sender=message.sender or "",
        action_type=action_type,
    )
    notes = [
        f"{c['name'].replace('_', ' ')}: {c['detail']}"
        if c.get("detail")
        else c["name"].replace("_", " ")
        for c in verdict["checks"]
        if not c["passed"] and not c.get("soft")
    ]
    source_text = f"{message.subject or ''}\n{message.body or ''}"
    findings = _deterministic_findings(draft_body, verdict, source_text=source_text)

    usage = None
    if live_llm and (budget is None or budget.allows()):
        usage = _model_findings(draft_body, message)
        if usage and usage.findings:
            findings.extend(usage.findings)
            notes.extend(f"unsupported claim: {f.claim}" for f in usage.findings)

    return Verdict(
        status=FLAGGED if notes else VERIFIED,
        notes=notes,
        findings=findings,
        cost_usd=usage.cost_usd if usage else 0.0,
        model=usage.model if usage else "",
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
    )
