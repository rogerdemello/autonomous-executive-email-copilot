"""Content-addressed store of model-written drafts, so a demo needs no network.

The problem this solves: the product's most compelling surface is model-written
prose, but a live demo is the worst possible place to depend on a model. Venue
wifi fails, keys expire, providers rate-limit, and a 30-second stall in front of
judges is unrecoverable.

So the model runs **once**, ahead of time (``seed_demo.py --with-llm``), and its
output is committed to disk. At demo time every lookup hits the cache, no
provider is constructed, and no socket opens — while what appears on screen is
genuinely what the model wrote.

Keys are a hash of the message content and the action, never of a row id. Two
consequences, both deliberate:

- Re-seeding a fresh database reuses the same drafts, because the content did not
  change.
- Editing a subject line in ``data/demo/inbox.json`` **misses**, because the
  content did. The README's claim that editing the fixture genuinely changes the
  output stays true rather than being papered over by a stale cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from app.core.paths import DEMO_DIR

logger = logging.getLogger(__name__)

DRAFT_CACHE_FILE = DEMO_DIR / "drafts.json"

_CACHE_VERSION = 1


def draft_key(*, provider_message_id: str, subject: str, body: str, action_type: str) -> str:
    """A stable id for "this exact message, drafted for this exact action"."""
    digest = hashlib.sha256()
    for part in (provider_message_id, subject, body, action_type):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")  # length-delimit, so fields cannot run together
    return digest.hexdigest()[:32]


class DraftCache:
    """A JSON file of drafts, loaded once and written only when asked."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DRAFT_CACHE_FILE
        self._entries: dict[str, dict] | None = None
        self._dirty = False

    # -- loading ------------------------------------------------------------
    def _load(self) -> dict[str, dict]:
        if self._entries is not None:
            return self._entries
        self._entries = {}
        if not self.path.exists():
            return self._entries
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt cache must degrade to "no cache", never break a sync.
            logger.warning("Ignoring unreadable draft cache %s: %s", self.path, exc)
            return self._entries
        entries = raw.get("drafts") if isinstance(raw, dict) else None
        if isinstance(entries, dict):
            self._entries = entries
        return self._entries

    # -- access -------------------------------------------------------------
    def get(self, key: str) -> dict | None:
        entry = self._load().get(key)
        if not isinstance(entry, dict) or not entry.get("body"):
            return None
        return entry

    def put(
        self,
        key: str,
        *,
        body: str,
        rationale: list[str] | None = None,
        confidence: float = 0.0,
        model: str = "",
        subject: str = "",
    ) -> None:
        self._load()[key] = {
            "body": body,
            "rationale": list(rationale or []),
            "confidence": confidence,
            "model": model,
            # Not read back — it makes the committed file reviewable by a human,
            # who would otherwise be diffing 32-character hashes.
            "subject": subject,
        }
        self._dirty = True

    def __len__(self) -> int:
        return len(self._load())

    # -- persistence --------------------------------------------------------
    def save(self, force: bool = False) -> bool:
        """Write the cache back. Returns whether anything was written."""
        if not self._dirty and not force:
            return False
        entries = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_comment": (
                "Model-written drafts, generated once by `python scripts/seed_demo.py "
                "--with-llm` and committed so the demo runs with no network and no API "
                "key. Keys hash message content + action; editing a message in "
                "inbox.json invalidates its draft by design."
            ),
            "version": _CACHE_VERSION,
            "drafts": dict(sorted(entries.items())),
        }
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        self._dirty = False
        return True


_default_cache: DraftCache | None = None


def get_draft_cache() -> DraftCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = DraftCache()
    return _default_cache


def reset_draft_cache() -> None:
    """Drop the loaded cache (tests, and after writing a new file)."""
    global _default_cache
    _default_cache = None
