import logging
import time
from typing import Any
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)


class InMemoryRedis:
    """In-process mock for Redis when Redis is not installed or running locally."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}

    def _is_expired(self, key: str) -> bool:
        if key not in self._store:
            return True
        _, exp = self._store[key]
        if exp is not None and time.time() > exp:
            del self._store[key]
            return True
        return False

    async def get(self, key: str) -> str | None:
        if self._is_expired(key):
            return None
        return self._store[key][0]

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        exp = (time.time() + ex) if ex else None
        self._store[key] = (str(value), exp)
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def incr(self, key: str) -> int:
        if self._is_expired(key):
            current = 0
        else:
            try:
                current = int(self._store[key][0])
            except (ValueError, TypeError):
                current = 0
        new_val = current + 1
        exp = self._store.get(key, (None, None))[1]
        self._store[key] = (str(new_val), exp)
        return new_val

    async def expire(self, key: str, seconds: int) -> bool:
        if self._is_expired(key):
            return False
        val, _ = self._store[key]
        self._store[key] = (val, time.time() + seconds)
        return True

    async def ttl(self, key: str) -> int:
        if self._is_expired(key):
            return -2
        _, exp = self._store[key]
        if exp is None:
            return -1
        rem = int(exp - time.time())
        return max(rem, 0)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self._store.clear()


class HybridRedisClient:
    """Proxies to real Redis if reachable; automatically falls back to InMemoryRedis

    without timing out or raising errors on development machines without Redis/Docker.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._real_client: Redis | None = None
        self._memory_client = InMemoryRedis()
        self._use_memory = False
        self._checked = False

    async def _get_client(self) -> Any:
        if self._use_memory:
            return self._memory_client

        if not self._checked:
            self._checked = True
            try:
                self._real_client = Redis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_connect_timeout=0.5,
                    socket_timeout=0.5,
                )
                await self._real_client.ping()
                logger.info("Connected to Redis at %s", self._url)
                return self._real_client
            except Exception:
                self._use_memory = True
                logger.info("Redis is not running at %s. Automatically using in-memory store (no Docker/Redis needed).", self._url)
                if self._real_client:
                    try:
                        await self._real_client.aclose()
                    except Exception:
                        pass
                    self._real_client = None
                return self._memory_client

        return self._real_client if self._real_client is not None else self._memory_client

    async def get(self, key: str) -> str | None:
        client = await self._get_client()
        try:
            return await client.get(key)
        except RedisError:
            self._use_memory = True
            return await self._memory_client.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        client = await self._get_client()
        try:
            return await client.set(key, value, ex=ex)
        except RedisError:
            self._use_memory = True
            return await self._memory_client.set(key, value, ex=ex)

    async def delete(self, *keys: str) -> int:
        client = await self._get_client()
        try:
            return await client.delete(*keys)
        except RedisError:
            self._use_memory = True
            return await self._memory_client.delete(*keys)

    async def incr(self, key: str) -> int:
        client = await self._get_client()
        try:
            return await client.incr(key)
        except RedisError:
            self._use_memory = True
            return await self._memory_client.incr(key)

    async def expire(self, key: str, seconds: int) -> bool:
        client = await self._get_client()
        try:
            return await client.expire(key, seconds)
        except RedisError:
            self._use_memory = True
            return await self._memory_client.expire(key, seconds)

    async def ttl(self, key: str) -> int:
        client = await self._get_client()
        try:
            return await client.ttl(key)
        except RedisError:
            self._use_memory = True
            return await self._memory_client.ttl(key)

    async def ping(self) -> bool:
        client = await self._get_client()
        try:
            return await client.ping()
        except RedisError:
            self._use_memory = True
            return await self._memory_client.ping()

    async def aclose(self) -> None:
        if self._real_client:
            await self._real_client.aclose()
        await self._memory_client.aclose()


# Single shared hybrid client: transparently handles both real Redis and in-memory fallback.
redis_client = HybridRedisClient(settings.REDIS_URL)
