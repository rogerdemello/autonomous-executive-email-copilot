"""Extract commitments from email prose: who owes what, by when.

The one capability the market rewards that this repo did not have. Every
review of every competitor names the same gap — nothing tracks follow-ups —
and an executive's real failure mode is not a mis-triaged message, it is a
promise made three weeks ago that nobody wrote down.

Two directions, and the difference matters:

- **theirs** — a promise in a message *sent to you*. "I'll send the revised
  terms Friday." You are now waiting on someone, and the useful moment is
  Saturday morning when they haven't.
- **ours** — a promise in a reply *you approved and sent*. "We'll have a
  decision by Thursday." You now owe someone, and the useful moment is
  Wednesday.

Deliberately deterministic, like the rest of the decision layer
(:mod:`app.copilot.policy`, :mod:`app.copilot.enrich`): patterns over prose,
no model call, reproducible, free, and testable against fixed text. A model
could phrase these more gracefully; it could not be relied on to find them,
and a follow-up tracker that misses things is worse than none because you stop
checking manually.

The bar for extraction is deliberately high. A tracker that surfaces every
sentence containing "will" is noise, and noise in this surface is fatal: the
whole value is that a short list means something.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# Directions a commitment can point.
OURS = "ours"
THEIRS = "theirs"

# A commitment sentence is bounded: longer than this and the "promise" is a
# paragraph of context that happens to contain a modal verb.
MAX_COMMITMENT_CHARS = 240

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

# First-person promises. The subject has to be the writer — "they will send it"
# is not a commitment *by* the writer, and conflating the two is how a
# follow-up list fills with things nobody agreed to.
_PROMISE = re.compile(
    r"\b("
    r"i(?:'| a)?m going to|we(?:'| a)?re going to"
    r"|i'?ll|we'?ll"
    r"|i will|we will"
    r"|i shall|we shall"
    r"|i can have|we can have"
    r"|let me (?:send|get|check|confirm|pull|circulate|draft)"
    r"|i'?ve asked|we'?ve asked"
    r"|(?:i|we) (?:will )?(?:send|share|circulate|confirm|revert|follow up|come back|get back)"
    r")\b",
    re.IGNORECASE,
)

# Explicit statements of being blocked. These are commitments in the other
# direction: someone has told you they are not moving yet.
_BLOCKED = re.compile(
    r"\b(waiting (?:on|for)|blocked (?:on|by)|pending (?:on|from)?|once (?:we|i) hear back)\b",
    re.IGNORECASE,
)

# Requests aimed at the reader. "Please send the figures by Thursday" in an
# incoming message is something *you* now owe, even though nobody promised it.
_REQUEST = re.compile(
    r"\b(please (?:send|share|confirm|approve|review|sign|reply|let me know)"
    r"|could you (?:send|share|confirm|approve|review|sign)"
    r"|can you (?:send|share|confirm|approve|review|sign)"
    r"|(?:i|we) need (?:you|your)"
    r"|(?:i|we) (?:would|'d) (?:like|need) (?:you|your))\b",
    re.IGNORECASE,
)

# Negations that turn a promise into a non-promise.
_NEGATED = re.compile(r"\b(?:won'?t|will not|cannot|can'?t|unable to|no longer)\b", re.IGNORECASE)

# Boilerplate that reads like a promise and commits to nothing.
_BOILERPLATE = re.compile(
    r"\b(?:"
    r"i'?ll be in touch|we'?ll be in touch"
    r"|(?:i|we) (?:will )?(?:look|be looking) forward"
    r"|let me know if"
    r"|we'?ll see"
    r"|i'?ll leave (?:it|that) with you"
    r")\b",
    re.IGNORECASE,
)

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

_WEEKDAY_RE = re.compile(
    r"\b(?:on |by |before |this |next )?(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE
)
_RELATIVE_RE = re.compile(
    r"\b(today|tomorrow|tonight|this (?:morning|afternoon|evening|week)"
    r"|next week|end of (?:the )?(?:day|week|month)|eod|eow)\b",
    re.IGNORECASE,
)
_IN_N_RE = re.compile(r"\bin (\d{1,2}) (day|days|week|weeks|hour|hours)\b", re.IGNORECASE)
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)? (?:of )?(" + "|".join(_MONTHS) + r")\b", re.IGNORECASE
)
_MONTH_DAY_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r") (\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE
)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


@dataclass(frozen=True)
class Commitment:
    """One promise, with the sentence it came from.

    ``due_phrase`` is kept alongside ``due_at`` on purpose: "Friday" resolves
    to a date, but showing a reviewer the words the sender actually used is
    what lets them catch a bad resolution rather than trust it.
    """

    text: str
    direction: str
    due_at: str | None = None
    due_phrase: str | None = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "direction": self.direction,
            "due_at": self.due_at,
            "due_phrase": self.due_phrase,
        }


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s.strip()]


def _next_weekday(reference: date, weekday: int) -> date:
    """The next occurrence of ``weekday``, never today.

    "Friday" said on a Friday means the *next* one — a promise for a day that
    is already three-quarters over is not what anyone meant.
    """
    ahead = (weekday - reference.weekday()) % 7
    return reference + timedelta(days=ahead or 7)


def parse_due(text: str, *, now: datetime | None = None) -> tuple[str | None, str | None]:
    """Resolve a date phrase in ``text`` to ``(iso_date, phrase)``.

    Returns ``(None, None)`` when the text names no date. Being wrong here is
    worse than being silent: an invented deadline in a follow-up list is the
    same failure the draft verifier exists to catch, one surface over.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()

    match = _ISO_RE.search(text)
    if match:
        try:
            found = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return found.isoformat(), match.group(0)
        except ValueError:
            pass

    for pattern, order in ((_DAY_MONTH_RE, "dm"), (_MONTH_DAY_RE, "md")):
        match = pattern.search(text)
        if not match:
            continue
        day = int(match.group(1) if order == "dm" else match.group(2))
        month = _MONTHS[(match.group(2) if order == "dm" else match.group(1)).lower()]
        for year in (today.year, today.year + 1):
            try:
                found = date(year, month, day)
            except ValueError:
                break
            # A bare "25 September" in the past means next year's.
            if found >= today:
                return found.isoformat(), match.group(0)
        continue

    match = _RELATIVE_RE.search(text)
    if match:
        phrase = match.group(1).lower()
        if phrase in ("today", "tonight", "eod") or phrase.startswith("this morning"):
            return today.isoformat(), match.group(1)
        if phrase == "tomorrow":
            return (today + timedelta(days=1)).isoformat(), match.group(1)
        if phrase in ("end of day",) or phrase == "end of the day":
            return today.isoformat(), match.group(1)
        if phrase in ("eow", "end of week", "end of the week", "this week"):
            return _next_weekday(today, _WEEKDAYS["friday"]).isoformat(), match.group(1)
        if phrase == "next week":
            return (today + timedelta(days=7)).isoformat(), match.group(1)
        if phrase in ("end of month", "end of the month"):
            first_next = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            return (first_next - timedelta(days=1)).isoformat(), match.group(1)
        return None, match.group(1)

    match = _IN_N_RE.search(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        days = amount * 7 if unit.startswith("week") else (0 if unit.startswith("hour") else amount)
        return (today + timedelta(days=days)).isoformat(), match.group(0)

    match = _WEEKDAY_RE.search(text)
    if match:
        return _next_weekday(today, _WEEKDAYS[match.group(1).lower()]).isoformat(), match.group(1)

    return None, None


def _looks_like_a_commitment(sentence: str) -> bool:
    if len(sentence) > MAX_COMMITMENT_CHARS:
        return False
    if _BOILERPLATE.search(sentence):
        return False
    if _NEGATED.search(sentence):
        return False
    return bool(_PROMISE.search(sentence) or _BLOCKED.search(sentence))


def extract(
    text: str,
    *,
    direction: str,
    now: datetime | None = None,
    include_requests: bool = False,
) -> list[Commitment]:
    """Find the commitments in one piece of prose.

    ``direction`` says whose promise it is — see the module docstring; the
    caller knows whose text this is and this function does not try to guess.
    ``include_requests`` additionally treats "please send X by Thursday" as a
    commitment, which is right for an incoming message (it is a thing you now
    owe) and wrong for your own outgoing reply.
    """
    found: list[Commitment] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        is_promise = _looks_like_a_commitment(sentence)
        is_request = (
            include_requests
            and len(sentence) <= MAX_COMMITMENT_CHARS
            and bool(_REQUEST.search(sentence))
            and not _NEGATED.search(sentence)
        )
        if not (is_promise or is_request):
            continue
        key = sentence.lower()
        if key in seen:
            continue
        seen.add(key)
        due_at, due_phrase = parse_due(sentence, now=now)
        found.append(
            Commitment(
                text=sentence,
                # A request in an inbound message is something *we* now owe.
                direction=OURS if (is_request and not is_promise) else direction,
                due_at=due_at,
                due_phrase=due_phrase,
            )
        )
    return found


def is_overdue(due_at: str | None, *, now: datetime | None = None) -> bool:
    """True when a dated commitment's day has passed.

    An undated commitment is never overdue: guessing a deadline nobody stated
    and then nagging about it is worse than tracking it quietly.
    """
    if not due_at:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        return date.fromisoformat(due_at) < now.date()
    except ValueError:
        return False
