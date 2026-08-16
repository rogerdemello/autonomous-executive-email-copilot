from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_REDIS_AVAILABLE = False
try:
    import redis.asyncio as aioredis

    _REDIS_AVAILABLE = True
except ImportError:
    logger.info("redis not installed; install with: pip install redis")


class RedisCache:
    """Async Redis cache with graceful fallback to no-op.

    Usage::

        cache = RedisCache()
        await cache.set("key", {"answer": 42}, ttl=300)
        value = await cache.get("key")
    """

    def __init__(
        self,
        url: str | None = None,
        prefix: str = "exec-email:",
        default_ttl: int = 3600,
    ) -> None:
        self.prefix = prefix
        self.default_ttl = default_ttl
        self._url = url or os.environ.get("REDIS_URL", "")
        self._client: Any = None
        self._lock = threading.Lock()

    async def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        if not _REDIS_AVAILABLE or not self._url:
            return None
        async with self._lock:
            if self._client is None:
                try:
                    self._client = aioredis.from_url(
                        self._url,
                        decode_responses=True,
                        socket_connect_timeout=2,
                        socket_timeout=2,
                    )
                    await self._client.ping()
                    logger.info("Connected to Redis at %s", self._url)
                except Exception as exc:
                    logger.warning("Redis unavailable: %s; cache disabled", exc)
                    self._client = None
        return self._client

    def _key(self, raw: str) -> str:
        return f"{self.prefix}{raw}"

    async def get(self, key: str) -> Any | None:
        client = await self._connect()
        if client is None:
            return None
        try:
            raw = await client.get(self._key(key))
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Redis get error: %s", exc)
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        client = await self._connect()
        if client is None:
            return False
        try:
            raw = json.dumps(value, default=str)
            await client.set(self._key(key), raw, ex=ttl or self.default_ttl)
            return True
        except Exception as exc:
            logger.debug("Redis set error: %s", exc)
        return False

    async def delete(self, key: str) -> bool:
        client = await self._connect()
        if client is None:
            return False
        try:
            await client.delete(self._key(key))
            return True
        except Exception as exc:
            logger.debug("Redis delete error: %s", exc)
        return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None


redis_cache = RedisCache()
