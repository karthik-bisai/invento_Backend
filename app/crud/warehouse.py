import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import ConflictError, paginate, remove, save
from app.models.warehouse import Warehouse
from app.schemas.warehouse import WarehouseCreate, WarehouseUpdate


async def get(db: AsyncSession, warehouse_id: uuid.UUID) -> Warehouse | None:
    return await db.get(Warehouse, warehouse_id)


async def list_warehouses(
    db: AsyncSession, page: int, size: int, q: str | None = None, organization_id: uuid.UUID | None = None
) -> tuple[list[Warehouse], int]:
    stmt = select(Warehouse).order_by(Warehouse.name, Warehouse.id)
    if organization_id:
        stmt = stmt.where(Warehouse.organization_id == organization_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Warehouse.name.ilike(like), Warehouse.location.ilike(like)))
    return await paginate(db, stmt, page, size)


async def create(db: AsyncSession, data: WarehouseCreate, organization_id: uuid.UUID | None = None) -> Warehouse:
    existing = await db.scalar(
        select(Warehouse).where(
            Warehouse.name == data.name,
            Warehouse.organization_id == organization_id if organization_id is not None else Warehouse.organization_id.is_(None),
        )
    )
    if existing:
        raise ConflictError("Warehouse name already exists")
    warehouse = Warehouse(**data.model_dump(), organization_id=organization_id)
    await save(db, warehouse)
    return warehouse


async def update(db: AsyncSession, warehouse: Warehouse, data: WarehouseUpdate) -> Warehouse:
    dump = data.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in dump and dump["name"] != warehouse.name:
        existing = await db.scalar(
            select(Warehouse).where(
                Warehouse.name == dump["name"],
                Warehouse.organization_id == warehouse.organization_id if warehouse.organization_id is not None else Warehouse.organization_id.is_(None),
            )
        )
        if existing:
            raise ConflictError("Warehouse name already exists")
    for field, value in dump.items():
        setattr(warehouse, field, value)
    await save(db, warehouse)
    return warehouse


async def delete(db: AsyncSession, warehouse: Warehouse) -> None:
    await remove(db, warehouse)
