import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_redis, require_roles
from app.api.rate_limit import enforce
from app.core.config import settings
from app.core.security import create_access_token
from app.crud import access_request as crud_access
from app.crud import notification as crud_notification
from app.crud import organization as crud_org
from app.crud import role as crud_role
from app.crud import user as crud_user
from app.models.access_request import RequestStatus
from app.models.user import Role, User
from app.services.account_status import account_block
from app.schemas.common import Page, PageParams
from app.schemas.token import Token
from app.schemas.user import (
    AdminRegister,
    AvatarUpdate,
    EmployeeRegister,
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
    user_role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return Token(access_token=create_access_token(str(user.id), user_role))


@router.post("/register/admin", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_admin(
    data: AdminRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    """Administrator registration: creates an organization and admin account (pending OTP verification)."""
    await enforce(request, redis, "register", settings.REGISTRATIONS_PER_HOUR, 3600)
    if await crud_user.get_by_email(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")
    org = await crud_org.create(db, name=data.organization_name, location=data.organization_location)
    return await crud_user.create(
        db,
        data,
        is_active=True,
        email_verified=False,
        organization_id=org.id,
        role=Role.ADMIN,
    )


@router.post("/register/employee", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_employee(
    data: EmployeeRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    """Employee registration: validates Organization ID and creates a pending access request."""
    await enforce(request, redis, "register", settings.REGISTRATIONS_PER_HOUR, 3600)
    if await crud_user.get_by_email(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")
    org = await crud_org.get_by_code(db, data.org_code)
    if org is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Invalid Organization ID. Please check the ID provided by your administrator.",
        )
    user = await crud_user.create(
        db,
        data,
        is_active=False,
        email_verified=False,
        organization_id=org.id,
        role=Role.WAREHOUSE_STAFF,
    )
    await crud_access.open_for(db, user, organization_id=org.id)
    admins = await crud_user.list_admins(db, organization_id=org.id)
    who = user.full_name or user.email
    crud_notification.add_for_all(
        db,
        admins,
        "access_requested",
        "New access request",
        f"{who} requested to join {org.name}.",
        "/users?tab=requests",
        organization_id=org.id,
    )
    await db.commit()
    return user


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    """Public sign-up supporting generic, admin, or employee registration."""
    await enforce(request, redis, "register", settings.REGISTRATIONS_PER_HOUR, 3600)
    if await crud_user.get_by_email(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")

    if data.organization_name:
        org = await crud_org.create(db, name=data.organization_name, location=data.organization_location)
        return await crud_user.create(
            db,
            data,
            is_active=True,
            email_verified=False,
            organization_id=org.id,
            role=Role.ADMIN,
        )

    if data.org_code:
        org = await crud_org.get_by_code(db, data.org_code)
        if org is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail="Invalid Organization ID. Please check the ID provided by your administrator.",
            )
        user = await crud_user.create(
            db,
            data,
            is_active=False,
            email_verified=False,
            organization_id=org.id,
            role=Role.WAREHOUSE_STAFF,
        )
        await crud_access.open_for(db, user, organization_id=org.id)
        admins = await crud_user.list_admins(db, organization_id=org.id)
        who = user.full_name or user.email
        crud_notification.add_for_all(
            db,
            admins,
            "access_requested",
            "New access request",
            f"{who} requested to join {org.name}.",
            "/users?tab=requests",
            organization_id=org.id,
        )
        await db.commit()
        return user

    return await crud_user.create(db, data, is_active=settings.SIGNUP_AUTO_ACTIVATE, email_verified=False)


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> UserResponse:
    from app.crud.role import get_effective_permissions
    perms = await get_effective_permissions(db, user)
    resp = UserResponse.model_validate(user)
    resp.permissions = perms
    return resp


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
)
async def create_user(
    data: UserCreate,
    admin: User = Depends(require_roles([Role.ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> User:
    if data.role is not None:
        role_str = str(data.role.value if isinstance(data.role, Role) else data.role)
        is_valid = False
        try:
            Role(role_str)
            is_valid = True
        except ValueError:
            pass
        if not is_valid and admin.organization_id:
            org_role = await crud_role.get_role_by_name(db, admin.organization_id, role_str)
            if org_role is not None:
                is_valid = True
        if not is_valid:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid role: {data.role}")

    if await crud_user.get_by_email(db, data.email):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")
    return await crud_user.create(
        db,
        data,
        organization_id=admin.organization_id,
        role=data.role,
        is_active=True,
        email_verified=True,
    )


@router.get(
    "/users",
    response_model=Page[UserResponse],
)
async def list_users(
    params: PageParams = Depends(),
    q: str | None = Query(None, description="Search email or name"),
    admin: User = Depends(require_roles([Role.ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> Page[UserResponse]:
    items, total = await crud_user.list_users(db, params.page, params.size, q, organization_id=admin.organization_id)
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
    if admin.organization_id is not None and user.organization_id is not None and user.organization_id != admin.organization_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot manage users from another organization")
    if user.id == admin.id and (data.is_active is False or (data.role is not None and data.role != user.role)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You cannot deactivate or change the role of your own account")

    if data.role is not None:
        role_str = str(data.role.value if isinstance(data.role, Role) else data.role)
        is_valid = False
        try:
            Role(role_str)
            is_valid = True
        except ValueError:
            pass
        if not is_valid and admin.organization_id:
            org_role = await crud_role.get_role_by_name(db, admin.organization_id, role_str)
            if org_role is not None:
                is_valid = True
        if not is_valid:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid role: {data.role}")

    was_inactive = not user.is_active
    user = await crud_user.update(db, user, data)
    if was_inactive and user.is_active:  # activating from the Users page also settles a waiting access request
        pending = await crud_access.get_for_user(db, user.id)
        if pending and pending.status != RequestStatus.APPROVED:
            await crud_access.approve(db, pending, admin, user.role)
    return user
