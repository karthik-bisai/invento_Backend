import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.user import User


def add(
    db: AsyncSession,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    organization_id: uuid.UUID | None = None,
) -> Notification:
    """Stage a notification on the session; the caller commits together with whatever caused it."""
    note = Notification(user_id=user_id, organization_id=organization_id, kind=kind, title=title, body=body, link=link)
    db.add(note)
    return note


def add_for_all(
    db: AsyncSession,
    users: list[User],
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    organization_id: uuid.UUID | None = None,
) -> None:
    for user in users:
        add(db, user.id, kind, title, body, link, organization_id=organization_id or user.organization_id)


async def list_for_user(
    db: AsyncSession, user_id: uuid.UUID, limit: int = 30, unread_only: bool = False
) -> tuple[list[Notification], int]:
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id, Notification.dismissed_at.is_(None))
        .order_by(Notification.created_at.desc(), Notification.id)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    items = list(await db.scalars(stmt.limit(limit)))
    unread = await db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
    )
    return items, unread or 0


async def mark_read(db: AsyncSession, user_id: uuid.UUID, notification_id: uuid.UUID) -> bool:
    note = await db.get(Notification, notification_id)
    if note is None or note.user_id != user_id:
        return False
    if note.read_at is None:
        note.read_at = datetime.now(timezone.utc)
        await db.commit()
    return True


async def mark_all_read(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None), Notification.dismissed_at.is_(None))
        .values(read_at=datetime.now(timezone.utc))
    )
    await db.commit()


async def dismiss(db: AsyncSession, user_id: uuid.UUID, notification_id: uuid.UUID) -> bool:
    note = await db.get(Notification, notification_id)
    if note is None or note.user_id != user_id:
        return False
    if note.dismissed_at is None:
        note.dismissed_at = datetime.now(timezone.utc)
        await db.commit()
    return True
