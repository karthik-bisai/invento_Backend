import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.crud import notification as crud
from app.models.user import User
from app.schemas.access import NotificationPage, NotificationResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    unread: bool = Query(False, description="Filter for unread notifications only"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationPage:
    items, unread_count = await crud.list_for_user(db, user.id, limit, unread_only=unread)
    return NotificationPage(items=items, unread=unread_count)


# Declared before "/{id}/read" so "read-all" isn't parsed as a UUID.
@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Response:
    await crud.mark_all_read(db, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationResponse:
    note = await db.get(crud.Notification, notification_id)
    if note is None or note.user_id != user.id or note.dismissed_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return note


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_one(notification_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Response:
    if not await crud.mark_read(db, user.id, notification_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{notification_id}/clear", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_one(notification_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> Response:
    if not await crud.dismiss(db, user.id, notification_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
