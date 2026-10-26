import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_roles
from app.crud import access_request as crud
from app.models.access_request import AccessRequest, RequestStatus
from app.models.user import Role, User
from app.schemas.access import AccessRequestResponse, ApproveRequest, RejectRequest
from app.schemas.common import Page, PageParams

router = APIRouter(prefix="/access-requests", tags=["access-requests"])

ADMIN_ONLY = Depends(require_roles([Role.ADMIN]))


async def _pending_or_error(db: AsyncSession, request_id: uuid.UUID) -> AccessRequest:
    request = await crud.get(db, request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Access request not found")
    if request.status != RequestStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"This request was already {request.status.value.lower()}")
    return request


@router.get("", response_model=Page[AccessRequestResponse], dependencies=[ADMIN_ONLY])
async def list_requests(
    params: PageParams = Depends(),
    request_status: RequestStatus | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> Page[AccessRequestResponse]:
    items, total = await crud.list_requests(db, params.page, params.size, request_status)
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.post("/{request_id}/approve", response_model=AccessRequestResponse)
async def approve(
    request_id: uuid.UUID,
    data: ApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_roles([Role.ADMIN])),
) -> AccessRequest:
    request = await _pending_or_error(db, request_id)
    return await crud.approve(db, request, admin, (data or ApproveRequest()).role)


@router.post("/{request_id}/reject", response_model=AccessRequestResponse)
async def reject(
    request_id: uuid.UUID,
    data: RejectRequest | None = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_roles([Role.ADMIN])),
) -> AccessRequest:
    request = await _pending_or_error(db, request_id)
    return await crud.reject(db, request, admin, (data or RejectRequest()).note)
