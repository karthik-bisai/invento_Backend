import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.crud.base import paginate, save
from app.models.user import Role, User
from app.schemas.user import Preferences, PreferencesUpdate, ProfileUpdate, UserCreate, UserRegister, UserUpdate


async def get(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def list_users(
    db: AsyncSession, page: int, size: int, q: str | None = None, organization_id: uuid.UUID | None = None
) -> tuple[list[User], int]:
    stmt = select(User).order_by(User.email, User.id)
    if organization_id:
        stmt = stmt.where(User.organization_id == organization_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.email.ilike(like) | User.full_name.ilike(like))
    return await paginate(db, stmt, page, size)


async def list_admins(db: AsyncSession, organization_id: uuid.UUID | None = None) -> list[User]:
    stmt = select(User).where(User.role == Role.ADMIN, User.is_active.is_(True)).order_by(User.email)
    if organization_id:
        stmt = stmt.where(User.organization_id == organization_id)
    return list(await db.scalars(stmt))


async def update(db: AsyncSession, user: User, data: UserUpdate) -> User:
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    password = changes.pop("password", None)
    for field, value in changes.items():
        setattr(user, field, value)
    if password:
        user.hashed_password = hash_password(password)
    await save(db, user)
    return user


async def update_profile(db: AsyncSession, user: User, data: ProfileUpdate) -> User:
    changes = data.model_dump(exclude_unset=True)
    if str(user.role.value if isinstance(user.role, Role) else user.role).upper() != "ADMIN":
        changes.pop("job_title", None)
        changes.pop("department", None)
    for field, value in changes.items():
        setattr(user, field, value)
    await save(db, user)
    return user


async def update_preferences(db: AsyncSession, user: User, data: PreferencesUpdate) -> User:
    merged = Preferences.model_validate(user.preferences or {}).model_copy(update=data.model_dump(exclude_none=True))
    user.preferences = merged.model_dump()  # assign a new dict so SQLAlchemy sees the change
    await save(db, user)
    return user


async def set_avatar(db: AsyncSession, user: User, url: str | None) -> User:
    user.avatar_url = url
    await save(db, user)
    return user


async def change_password(db: AsyncSession, user: User, current: str, new: str) -> bool:
    """False when `current` is wrong."""
    if not verify_password(current, user.hashed_password):
        return False
    user.hashed_password = hash_password(new)
    await save(db, user)
    return True


async def set_password(db: AsyncSession, user: User, new: str) -> User:
    user.hashed_password = hash_password(new)
    await save(db, user)
    return user


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email.lower()))


async def create(
    db: AsyncSession,
    data: UserCreate | UserRegister,
    *,
    is_active: bool = True,
    email_verified: bool = True,
    organization_id: uuid.UUID | None = None,
    role: Role | None = None,
) -> User:
    assigned_role = role or getattr(data, "role", Role.WAREHOUSE_STAFF)
    user = User(
        email=data.email.lower(),
        hashed_password=hash_password(data.password),
        role=assigned_role,
        full_name=data.full_name,
        job_title=getattr(data, "job_title", None),
        department=getattr(data, "department", None),
        organization_id=organization_id,
        is_active=is_active,
        email_verified=email_verified,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


_DUMMY_HASH = hash_password("timing-equaliser")


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await get_by_email(db, email)
    # Always run one bcrypt verification so response time doesn't reveal which emails exist.
    if not verify_password(password, user.hashed_password if user else _DUMMY_HASH) or user is None:
        return None
    return user
