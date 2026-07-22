from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from .redis_cache import redis_cache

logger = logging.getLogger(__name__)

_EMBEDDING_AVAILABLE = False
try:
    from openai import OpenAI as _OpenAI

    _EMBEDDING_AVAILABLE = True
except ImportError:
    pass


class SemanticCache:
    """Semantic similarity cache for LLM responses.

    Uses embedding vectors to find semantically similar cached responses,
    keyed by cosine similarity above a threshold.

    Two-layer lookup:
    1. Exact hash match (fast path, Redis)
    2. Semantic nearest-neighbor above threshold (embedding comparison)
    """

    def __init__(
        self,
        threshold: float = 0.95,
        model: str = "text-embedding-ada-002",
        max_candidates: int = 100,
    ) -> None:
        self.threshold = threshold
        self.model = model
        self.max_candidates = max_candidates
        self._client: Any = None

    def _get_openai(self) -> Any:
        if self._client is None and _EMBEDDING_AVAILABLE:
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if api_key:
                self._client = _OpenAI(api_key=api_key)
        return self._client

    def _compute_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def _cosine_sim(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    async def get(self, observation_text: str) -> Any | None:
        """Look up semantically similar cached response."""
        obs_hash = self._compute_hash(observation_text)

        exact = await redis_cache.get(f"semantic:{obs_hash}")
        if exact is not None:
            return exact

        embedding = await self._get_embedding(observation_text)
        if embedding is None:
            return None

        candidates = await redis_cache.get("semantic:candidates") or []
        best_sim = 0.0
        best_value = None
        for entry in candidates[-self.max_candidates :]:
            sim = self._cosine_sim(embedding, entry["embedding"])
            if sim > best_sim:
                best_sim = sim
                best_value = entry.get("value")

        if best_sim >= self.threshold and best_value is not None:
            return best_value
        return None

    async def set(
        self,
        observation_text: str,
        response: Any,
        embedding: list[float] | None = None,
    ) -> None:
        obs_hash = self._compute_hash(observation_text)
        await redis_cache.set(f"semantic:{obs_hash}", response)

        if embedding is None:
            embedding = await self._get_embedding(observation_text)
        if embedding is not None:
            candidates = await redis_cache.get("semantic:candidates") or []
            candidates.append(
                {
                    "hash": obs_hash,
                    "embedding": embedding,
                    "value": response,
                }
            )
            if len(candidates) > self.max_candidates:
                candidates = candidates[-self.max_candidates :]
            await redis_cache.set("semantic:candidates", candidates)

    async def _get_embedding(self, text: str) -> list[float] | None:
        client = self._get_openai()
        if client is None:
            return None
        try:
            resp = client.embeddings.create(input=text, model=self.model)
            return resp.data[0].embedding
        except Exception as exc:
            logger.debug("Embedding error: %s", exc)
        return None

    async def clear(self) -> None:
        await redis_cache.delete("semantic:candidates")


semantic_cache = SemanticCache()
