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

The verdict is stored on the action (``verification_status`` +
``verification_notes``) and rendered as a chip in the approvals queue: the
reviewer sees "verified" or exactly what was flagged, next to the approve
button. Nothing here blocks an action — a flagged draft still queues, because
the human is the gate; verification just tells them where to look first.
"""

from __future__ import annotations

import logging

from app.copilot.providers.base import FetchedMessage

logger = logging.getLogger(__name__)

VERIFIED = "verified"
FLAGGED = "flagged"

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
Answer in JSON only: {{"unsupported": ["..."]}} — an empty list if everything
is supported."""


def _model_unsupported_claims(draft_body: str, message: FetchedMessage) -> list[str] | None:
    """The model layer. Returns None when unavailable — never raises."""
    try:
        from app.llm.parsing import extract_json_object
        from app.llm.providers import auto_detect_provider

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
            max_tokens=300,
        )
        parsed = extract_json_object(response.content or "")
        if parsed is None or not isinstance(parsed.get("unsupported"), list):
            return None
        return [str(claim).strip() for claim in parsed["unsupported"] if str(claim).strip()][:6]
    except Exception as exc:  # noqa: BLE001 - verification must never break a sync
        logger.info("Model verification unavailable: %s", exc)
        return None


def verify_draft(
    draft_body: str,
    *,
    message: FetchedMessage,
    action_type: str,
    live_llm: bool = False,
) -> tuple[str, list[str]]:
    """Verify one draft. Returns ``(status, notes)``.

    ``status`` is :data:`VERIFIED` or :data:`FLAGGED`; ``notes`` say exactly
    what was flagged (empty when verified). Soft rubric warnings do not flag on
    their own — they exist for the eval report, not the reviewer.
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

    if live_llm:
        unsupported = _model_unsupported_claims(draft_body, message)
        if unsupported:
            notes.extend(f"unsupported claim: {claim}" for claim in unsupported)

    return (FLAGGED if notes else VERIFIED), notes
