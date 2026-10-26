from redis.asyncio import Redis

from app.core.config import settings

# Short timeouts so a Redis outage degrades to "no cache" instead of stalling requests.
redis_client: Redis = Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)
