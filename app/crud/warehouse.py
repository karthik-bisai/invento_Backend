import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import paginate, remove, save
from app.models.warehouse import Warehouse
from app.schemas.warehouse import WarehouseCreate, WarehouseUpdate


async def get(db: AsyncSession, warehouse_id: uuid.UUID) -> Warehouse | None:
    return await db.get(Warehouse, warehouse_id)


async def list_warehouses(
    db: AsyncSession, page: int, size: int, q: str | None = None
) -> tuple[list[Warehouse], int]:
    stmt = select(Warehouse).order_by(Warehouse.name, Warehouse.id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Warehouse.name.ilike(like), Warehouse.location.ilike(like)))
    return await paginate(db, stmt, page, size)


async def create(db: AsyncSession, data: WarehouseCreate) -> Warehouse:
    warehouse = Warehouse(**data.model_dump())
    await save(db, warehouse)
    return warehouse


async def update(db: AsyncSession, warehouse: Warehouse, data: WarehouseUpdate) -> Warehouse:
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(warehouse, field, value)
    await save(db, warehouse)
    return warehouse


async def delete(db: AsyncSession, warehouse: Warehouse) -> None:
    await remove(db, warehouse)
