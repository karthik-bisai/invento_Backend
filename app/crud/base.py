from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class ConflictError(Exception):
    """Raised when a write violates a unique/foreign-key/check constraint."""


async def paginate(
    db: AsyncSession, stmt: Select[Any], page: int, size: int, *, rows: bool = False
) -> tuple[list[Any], int]:
    """Return (page_items, total). Pass rows=True for multi-column selects (returns Row objects)."""
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    paged = stmt.limit(size).offset((page - 1) * size)
    result = (await db.execute(paged)).all() if rows else (await db.scalars(paged)).all()
    return list(result), total


async def save(db: AsyncSession, obj: ModelT | None = None) -> None:
    """Add (optional) and commit, translating constraint violations to ConflictError."""
    try:
        if obj is not None:
            db.add(obj)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(str(exc.orig)) from exc
    if obj is not None:
        await db.refresh(obj)


async def remove(db: AsyncSession, obj: Base) -> None:
    try:
        await db.delete(obj)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(str(exc.orig)) from exc
