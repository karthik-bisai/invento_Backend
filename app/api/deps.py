import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import redis_client
from app.core.security import decode_access_token
from app.models.user import Role, User

bearer_scheme = HTTPBearer(auto_error=False)

__all__ = ["get_db", "get_redis", "get_current_user", "require_roles", "require_permission"]


def get_redis() -> Redis:
    return redis_client


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    payload = decode_access_token(credentials.credentials)
    if payload is None or "sub" not in payload:
        raise unauthorized
    try:
        user_id = uuid.UUID(payload["sub"])
    except ValueError:
        raise unauthorized
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_roles(allowed: list[Role | str]) -> Callable[..., Awaitable[User]]:
    allowed_roles = {str(r.value if isinstance(r, Role) else r).upper() for r in allowed}

    async def checker(user: User = Depends(get_current_user)) -> User:
        user_role_str = str(user.role.value if isinstance(user.role, Role) else user.role).upper()
        if user_role_str not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return checker


def require_permission(permission: str) -> Callable[..., Awaitable[User]]:
    async def checker(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> User:
        user_role_str = str(user.role.value if isinstance(user.role, Role) else user.role).upper()
        if user_role_str == "ADMIN":
            return user
        from app.crud.role import get_effective_permissions
        perms = await get_effective_permissions(db, user)
        if permission not in perms:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return checker

