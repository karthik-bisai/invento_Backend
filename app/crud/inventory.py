import uuid

from typing import Any

from sqlalchemy import Row, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import paginate, remove, save
from app.models.inventory import InventoryItem
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.schemas.inventory import InventoryItemCreate, InventoryItemUpdate


async def get(db: AsyncSession, item_id: uuid.UUID, for_update: bool = False) -> InventoryItem | None:
    return await db.get(InventoryItem, item_id, with_for_update=for_update)


SORT_COLUMNS = {
    "sku": Product.sku,
    "name": Product.name,
    "warehouse": Warehouse.name,
    "quantity_on_hand": InventoryItem.quantity_on_hand,
    "reserved_quantity": InventoryItem.reserved_quantity,
    "available_quantity": InventoryItem.quantity_on_hand - InventoryItem.reserved_quantity,
}


async def list_items(
    db: AsyncSession,
    page: int,
    size: int,
    product_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    q: str | None = None,
    low_stock: bool = False,
    sort_by: str = "name",
    order: str = "asc",
) -> tuple[list[Row[Any]], int]:
    """Inventory rows joined with product/warehouse names and the product's total across warehouses."""
    totals = (
        select(InventoryItem.product_id.label("pid"), func.sum(InventoryItem.quantity_on_hand).label("total"))
        .group_by(InventoryItem.product_id)
        .subquery()
    )
    stmt = (
        select(
            InventoryItem.id,
            InventoryItem.product_id,
            InventoryItem.warehouse_id,
            InventoryItem.quantity_on_hand,
            InventoryItem.reserved_quantity,
            Product.sku,
            Product.name.label("product_name"),
            Product.category,
            Product.unit_price,
            Product.reorder_level,
            Warehouse.name.label("warehouse_name"),
            totals.c.total.label("product_total_quantity"),
        )
        .join(Product, Product.id == InventoryItem.product_id)
        .join(Warehouse, Warehouse.id == InventoryItem.warehouse_id)
        .join(totals, totals.c.pid == InventoryItem.product_id)
    )
    if product_id:
        stmt = stmt.where(InventoryItem.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(InventoryItem.warehouse_id == warehouse_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    if low_stock:
        stmt = stmt.where(Product.reorder_level > 0, totals.c.total <= Product.reorder_level)

    column = SORT_COLUMNS[sort_by]
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc(), InventoryItem.id)
    return await paginate(db, stmt, page, size, rows=True)


async def create(db: AsyncSession, data: InventoryItemCreate) -> InventoryItem:
    item = InventoryItem(**data.model_dump())
    await save(db, item)
    return item


async def update(db: AsyncSession, item: InventoryItem, data: InventoryItemUpdate) -> InventoryItem:
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(item, field, value)
    await save(db, item)
    return item


async def delete(db: AsyncSession, item: InventoryItem) -> None:
    await remove(db, item)
