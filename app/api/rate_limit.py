"""Fixed-window rate limiting for the unauthenticated endpoints (login, sign-up).

Counters live in Redis so limits hold across workers. If Redis is down we fall back to a per-process
counter instead of failing open, so brute-force protection degrades rather than disappears.
"""

import time

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings

_local: dict[str, tuple[int, float]] = {}  # key -> (count, window_ends_at)
_LOCAL_MAX_KEYS = 10_000


def client_ip(request: Request) -> str:
    """Caller address. X-Forwarded-For is only honoured when TRUST_PROXY_HEADERS is set (i.e. behind our own proxy)."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _hit_local(key: str, window: int) -> tuple[int, int]:
    now = time.monotonic()
    if len(_local) > _LOCAL_MAX_KEYS:
        for k in [k for k, (_, end) in _local.items() if end <= now]:
            del _local[k]
    count, end = _local.get(key, (0, 0.0))
    if end <= now:
        count, end = 0, now + window
    _local[key] = (count + 1, end)
    return count + 1, max(int(end - now), 1)


async def _hit(redis: Redis, key: str, window: int) -> tuple[int, int]:
    """Count one hit. Returns (hits in this window, seconds until the window resets)."""
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        ttl = await redis.ttl(key)
        if ttl is None or ttl < 0:  # key lost its expiry (crash between INCR and EXPIRE): never lock out forever
            await redis.expire(key, window)
            ttl = window
        return count, ttl
    except RedisError:
        return _hit_local(key, window)


async def enforce(request: Request, redis: Redis, scope: str, limit: int, window: int, subject: str | None = None) -> None:
    """Raise 429 once `limit` hits per `window` seconds is exceeded for this caller (and optional subject, e.g. an email)."""
    if not settings.RATE_LIMIT_ENABLED:
        return
    who = client_ip(request) + (f":{subject.lower()}" if subject else "")
    count, retry_after = await _hit(redis, f"rl:{scope}:{who}", window)
    if count > limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {retry_after} second{'s' if retry_after != 1 else ''}.",
            headers={"Retry-After": str(retry_after)},
        )


def reset_local_state() -> None:
    _local.clear()
