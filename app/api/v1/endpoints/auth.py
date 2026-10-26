import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_redis, require_roles
from app.api.rate_limit import enforce
from app.core.config import settings
from app.core.security import create_access_token
from app.crud import access_request as crud_access
from app.crud import user as crud_user
from app.models.access_request import RequestStatus
from app.models.user import Role, User
from app.services.account_status import account_block
from app.schemas.common import Page, PageParams
from app.schemas.token import Token
from app.schemas.user import (
    AvatarUpdate,
    PasswordChange,
    PreferencesUpdate,
    ProfileUpdate,
    UserCreate,
    UserLogin,
    UserRegister,
    UserResponse,
    UserUpdate,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
async def login(
    data: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Token:
    await enforce(request, redis, "login-ip", settings.LOGIN_ATTEMPTS_PER_MINUTE_PER_IP, 60)
    await enforce(request, redis, "login", settings.LOGIN_ATTEMPTS_PER_MINUTE, 60, subject=data.email)
    user = await crud_user.authenticate(db, data.email, data.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if (block := await account_block(db, user)) is not None:
        # Only reached with the right password, so naming the reason reveals nothing to a guesser.
        code, message = block
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=message, headers={"X-Account-Status": code})
    return Token(access_token=create_access_token(str(user.id), user.role.value))


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    """Public sign-up. Always the lowest role, unverified and inactive until the email is confirmed and an admin approves."""
    await enforce(request, redis, "register", settings.REGISTRATIONS_PER_HOUR, 3600)
    if await crud_user.get_by_email(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")
    return await crud_user.create(db, data, is_active=settings.SIGNUP_AUTO_ACTIVATE, email_verified=False)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserResponse)
async def update_profile(data: ProfileUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> User:
    return await crud_user.update_profile(db, user, data)


@router.patch("/me/preferences", response_model=UserResponse)
async def update_preferences(
    data: PreferencesUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> User:
    return await crud_user.update_preferences(db, user, data)


@router.put("/me/avatar", response_model=UserResponse)
async def set_avatar(data: AvatarUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> User:
    return await crud_user.set_avatar(db, user, data.avatar_url)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChange,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    await enforce(request, redis, "pwchange", 5, 600, subject=str(user.id))
    if not await crud_user.change_password(db, user, data.current_password, data.new_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Your current password is incorrect")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles([Role.ADMIN]))],
)
async def create_user(data: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    if await crud_user.get_by_email(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")
    return await crud_user.create(db, data)


@router.get(
    "/users",
    response_model=Page[UserResponse],
    dependencies=[Depends(require_roles([Role.ADMIN]))],
)
async def list_users(
    params: PageParams = Depends(),
    q: str | None = Query(None, description="Search email or name"),
    db: AsyncSession = Depends(get_db),
) -> Page[UserResponse]:
    items, total = await crud_user.list_users(db, params.page, params.size, q)
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_roles([Role.ADMIN])),
) -> User:
    user = await crud_user.get(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id and (data.is_active is False or (data.role is not None and data.role != user.role)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate or change the role of your own account")
    was_inactive = not user.is_active
    user = await crud_user.update(db, user, data)
    if was_inactive and user.is_active:  # activating from the Users page also settles a waiting access request
        pending = await crud_access.get_for_user(db, user.id)
        if pending and pending.status != RequestStatus.APPROVED:
            await crud_access.approve(db, pending, admin, user.role)
    return user
