"""Tolerant JSON extraction from model output.

Models are asked for bare JSON and frequently return it wrapped — in a ```json
fence, or with a sentence of preamble in front. This walks from strictest to
loosest so a well-behaved response costs one ``json.loads`` and a chatty one
still parses.

Lifted out of :mod:`app.llm.agent` (where it was ``_parse_llm_response``) so the
drafter can reuse it without importing the simulator agent. ``agent`` keeps its
old private name as an alias, so its behaviour is unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_BRACED = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in ``text``, or ``None`` if there isn't one."""
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return parsed if isinstance(parsed, dict) else None

    for pattern in (_FENCED, _BRACED):
        match = pattern.search(text)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(1) if pattern is _FENCED else match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None
