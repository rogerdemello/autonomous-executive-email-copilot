"""Learning from the approval queue.

Every decision a reviewer makes — approve, amend-then-approve, reject — is a
labeled example: *the copilot proposed this action on a message shaped like
that, and a human ruled on it*. This module turns that corpus into three
concrete behaviours:

- **Routing feedback.** A (action_type, sender_role) pair the team keeps
  rejecting stops being proposed: the sync downgrades it to a deferral, with
  the reason recorded on the action. The threshold is deliberately blunt —
  at least :data:`MIN_DECISIONS` decisions and at least
  :data:`SUPPRESS_REJECTION_RATE` of them rejections — because a routing
  change should need clear, repeated evidence, not one bad afternoon.
- **Few-shot examples for the drafter.** Approved and (especially) edited
  drafts show the voice this org actually sends. The most recent ones ride
  along in the drafting prompt.
- **A "what the copilot learned" panel.** The same aggregation, rendered as
  sentences a reviewer can audit. Nothing here is a black box: every learned
  behaviour names the decisions it came from.

All of it is tenant-scoped and none of it touches the deterministic policy's
code path — learning adjusts *this org's* routing and prose, never the
benchmark or another tenant.
"""

from __future__ import annotations

from dataclasses import dataclass

from .repository import ProposedActionRepository

# How many recent decisions the learner reads. Old decisions age out naturally:
# the team that rejected escalations in March may want them back in June.
DECISION_WINDOW = 200

# A pair needs this many decisions before any behaviour changes.
MIN_DECISIONS = 3

# ... and this share of them must be rejections before the pair is suppressed.
SUPPRESS_REJECTION_RATE = 0.8


@dataclass(frozen=True)
class PairStats:
    action_type: str
    sender_role: str
    approved: int = 0
    edited: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.approved + self.edited + self.rejected

    @property
    def accepted(self) -> int:
        return self.approved + self.edited


class FeedbackService:
    """Reads the decision corpus; computes routing feedback, examples, insights."""

    def __init__(self) -> None:
        self.actions = ProposedActionRepository()

    def _decisions(self, org_id: str) -> list[dict]:
        return self.actions.list_decided(org_id, limit=DECISION_WINDOW)

    # -- aggregation ---------------------------------------------------------
    def _pair_stats(self, decisions: list[dict]) -> dict[tuple[str, str], PairStats]:
        counts: dict[tuple[str, str], dict[str, int]] = {}
        for d in decisions:
            role = d.get("sender_role") or "unknown"
            key = (d["action_type"], role)
            bucket = counts.setdefault(key, {"approved": 0, "edited": 0, "rejected": 0})
            outcome = d.get("outcome")
            if outcome in bucket:
                bucket[outcome] += 1
        return {
            (a, r): PairStats(action_type=a, sender_role=r, **c) for (a, r), c in counts.items()
        }

    def suppressed_pairs(self, org_id: str) -> dict[tuple[str, str], PairStats]:
        """(action_type, sender_role) pairs the org has plainly voted against."""
        stats = self._pair_stats(self._decisions(org_id))
        return {
            pair: s
            for pair, s in stats.items()
            if s.total >= MIN_DECISIONS and s.rejected / s.total >= SUPPRESS_REJECTION_RATE
        }

    def few_shot_examples(self, org_id: str, action_type: str, k: int = 3) -> list[dict]:
        """Recent accepted drafts of this action type, edited ones first.

        An edited draft is a human's correction of the copilot's wording — the
        strongest possible style signal — so edits outrank plain approvals at
        equal recency. Returns ``[{"subject", "body"}]`` ready for a prompt.
        """
        candidates = []
        for d in self._decisions(org_id):
            if d["action_type"] != action_type:
                continue
            if d.get("outcome") not in ("approved", "edited"):
                continue
            body = (d.get("content") or "").strip()
            if not body:
                continue
            candidates.append(d)
        # Decisions are already newest-first; a stable sort by edited-ness keeps
        # that recency order within each group.
        candidates.sort(key=lambda d: d.get("outcome") != "edited")
        return [
            {"subject": d.get("subject") or "", "body": (d.get("content") or "").strip()}
            for d in candidates[:k]
        ]

    def calibration_pairs(self, org_id: str) -> list[dict]:
        """(confidence, correct) pairs for the calibration report.

        The drafter states a confidence with every model-written draft; the
        approval queue supplies the ground truth. "Correct" is deliberately
        strict — the draft was sent *exactly as written*. An edited approval
        means the prose needed a human fix, so for calibration purposes the
        stated confidence was wrong. Feed the output to
        ``research/benchmark/calibration_cli.py`` (Brier score, ECE).
        """
        pairs = []
        for d in self._decisions(org_id):
            confidence = d.get("draft_confidence")
            if confidence is None:
                continue
            pairs.append(
                {"confidence": float(confidence), "correct": d.get("outcome") == "approved"}
            )
        return pairs

    # -- the reviewer-facing panel --------------------------------------------
    def insights(self, org_id: str) -> dict:
        """Aggregates plus plain-English sentences, for UI and API."""
        decisions = self._decisions(org_id)
        by_action: dict[str, dict[str, int]] = {}
        for d in decisions:
            bucket = by_action.setdefault(
                d["action_type"], {"approved": 0, "edited": 0, "rejected": 0}
            )
            if d.get("outcome") in bucket:
                bucket[d["outcome"]] += 1

        stats = self._pair_stats(decisions)
        suppressed = [
            s
            for s in stats.values()
            if s.total >= MIN_DECISIONS and s.rejected / s.total >= SUPPRESS_REJECTION_RATE
        ]
        trusted = [s for s in stats.values() if s.total >= MIN_DECISIONS and s.rejected == 0]

        learned: list[str] = []
        for s in sorted(suppressed, key=lambda s: -s.total):
            learned.append(
                f"Stopped proposing {s.action_type} for {s.sender_role} senders — "
                f"you rejected {s.rejected} of the last {s.total}. These are filed "
                f"as deferred instead."
            )
        for s in sorted(trusted, key=lambda s: -s.total):
            phrase = f"{s.accepted} of {s.total}" if s.edited else f"all {s.total}"
            learned.append(
                f"{s.action_type.capitalize()} drafts for {s.sender_role} senders are "
                f"landing — you accepted {phrase} recently"
                + (f" ({s.edited} with edits)." if s.edited else ".")
            )
        edited_total = sum(b["edited"] for b in by_action.values())
        if edited_total:
            learned.append(
                f"{edited_total} draft{'s' if edited_total != 1 else ''} you edited before "
                f"sending now guide the copilot's wording for this workspace."
            )

        return {
            "window": len(decisions),
            "by_action": {
                a: {
                    **c,
                    "total": sum(c.values()),
                    "acceptance_rate": (
                        round((c["approved"] + c["edited"]) / sum(c.values()), 3)
                        if sum(c.values())
                        else None
                    ),
                }
                for a, c in by_action.items()
            },
            "suppressed": [
                {
                    "action_type": s.action_type,
                    "sender_role": s.sender_role,
                    "rejected": s.rejected,
                    "total": s.total,
                }
                for s in suppressed
            ],
            "trusted": [
                {
                    "action_type": s.action_type,
                    "sender_role": s.sender_role,
                    "accepted": s.accepted,
                    "total": s.total,
                }
                for s in trusted
            ],
            "learned": learned,
        }
