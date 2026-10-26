from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import redis_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Postgres is required. Redis only caches and throttles, so its absence degrades the service but doesn't fail it."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "down", "postgres": "down", "redis": "unknown"}
    try:
        await redis_client.ping()
        redis = "ok"
    except Exception:
        redis = "down"
    return {"status": "ok" if redis == "ok" else "degraded", "postgres": "ok", "redis": redis}
