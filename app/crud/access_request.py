import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import notification as crud_notification
from app.crud.base import paginate, save
from app.models.access_request import AccessRequest, RequestStatus
from app.models.user import Role, User


async def get(db: AsyncSession, request_id: uuid.UUID) -> AccessRequest | None:
    return await db.get(AccessRequest, request_id)


async def get_for_user(db: AsyncSession, user_id: uuid.UUID) -> AccessRequest | None:
    return await db.scalar(select(AccessRequest).where(AccessRequest.user_id == user_id))


async def list_requests(
    db: AsyncSession, page: int, size: int, status: RequestStatus | None = None, organization_id: uuid.UUID | None = None
) -> tuple[list[AccessRequest], int]:
    stmt = select(AccessRequest).order_by(AccessRequest.created_at.desc(), AccessRequest.id)
    if organization_id:
        stmt = stmt.where(AccessRequest.organization_id == organization_id)
    if status:
        stmt = stmt.where(AccessRequest.status == status)
    return await paginate(db, stmt, page, size)


async def open_for(db: AsyncSession, user: User, organization_id: uuid.UUID | None = None) -> AccessRequest:
    """The user's request, creating a pending one if none exists. Not committed."""
    existing = await get_for_user(db, user.id)
    if existing:
        return existing
    org_id = organization_id or user.organization_id
    request = AccessRequest(user_id=user.id, organization_id=org_id, status=RequestStatus.PENDING)
    db.add(request)
    await db.flush()
    await db.refresh(request, attribute_names=["user"])
    return request


async def approve(db: AsyncSession, request: AccessRequest, admin: User, role: Role = Role.WAREHOUSE_STAFF) -> AccessRequest:
    request.status = RequestStatus.APPROVED
    request.decided_by_id = admin.id
    request.decided_at = datetime.now(timezone.utc)
    request.note = None
    request.user.is_active = True
    request.user.role = role
    crud_notification.add(
        db, request.user_id, "access_approved", "Your access was approved", "You can now use the workspace.", "/dashboard"
    )
    await save(db, request)
    return request


async def reject(db: AsyncSession, request: AccessRequest, admin: User, note: str | None) -> AccessRequest:
    request.status = RequestStatus.REJECTED
    request.decided_by_id = admin.id
    request.decided_at = datetime.now(timezone.utc)
    request.note = note
    request.user.is_active = False
    crud_notification.add(db, request.user_id, "access_rejected", "Your access request was declined", note)
    await save(db, request)
    return request
