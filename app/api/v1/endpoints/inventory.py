import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_redis, require_roles
from app.crud import inventory as crud
from app.crud import product as crud_product
from app.crud import warehouse as crud_warehouse
from app.crud.base import ConflictError
from app.models.inventory import InventoryItem
from app.models.user import Role
from app.schemas.common import Page, PageParams
from app.schemas.inventory import (
    InventoryItemCreate,
    InventoryItemResponse,
    InventoryItemUpdate,
    InventoryRow,
    InventorySummary,
    LowStockItem,
)
from app.services.inventory_summary import get_inventory_summary, invalidate_inventory_summary, list_low_stock

router = APIRouter(prefix="/inventory", tags=["inventory"])

ALL_ROLES = [Role.ADMIN, Role.INVENTORY_MANAGER, Role.WAREHOUSE_STAFF]


def _check_quantities(on_hand: int, reserved: int) -> None:
    if reserved > on_hand:
        raise HTTPException(422, detail="reserved_quantity cannot exceed quantity_on_hand")


# NOTE: declared before "/{item_id}" so "summary" isn't parsed as a UUID.
@router.get("/summary", response_model=InventorySummary, dependencies=[Depends(get_current_user)])
async def inventory_summary(
    db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)
) -> InventorySummary:
    return await get_inventory_summary(db, redis)


@router.get("/low-stock", response_model=list[LowStockItem], dependencies=[Depends(get_current_user)])
async def low_stock(limit: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db)) -> list[LowStockItem]:
    return await list_low_stock(db, limit)


@router.get("", response_model=Page[InventoryRow], dependencies=[Depends(get_current_user)])
async def list_inventory(
    params: PageParams = Depends(),
    product_id: uuid.UUID | None = Query(None),
    warehouse_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, description="Search product name or SKU"),
    low_stock: bool = Query(False, description="Only products at/below their reorder level"),
    sort_by: Literal["sku", "name", "warehouse", "quantity_on_hand", "reserved_quantity", "available_quantity"] = "name",
    order: Literal["asc", "desc"] = "asc",
    db: AsyncSession = Depends(get_db),
) -> Page[InventoryRow]:
    rows, total = await crud.list_items(
        db, params.page, params.size, product_id, warehouse_id, q, low_stock, sort_by, order
    )
    return Page(items=[InventoryRow.model_validate(r, from_attributes=True) for r in rows], total=total, page=params.page, size=params.size)


@router.get("/{item_id}", response_model=InventoryItemResponse, dependencies=[Depends(get_current_user)])
async def get_item(item_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> InventoryItem:
    item = await crud.get(db, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inventory item not found")
    return item


@router.post(
    "",
    response_model=InventoryItemResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles([Role.ADMIN, Role.INVENTORY_MANAGER]))],
)
async def create_item(
    data: InventoryItemCreate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> InventoryItem:
    if await crud_product.get(db, data.product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    if await crud_warehouse.get(db, data.warehouse_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
    _check_quantities(data.quantity_on_hand, data.reserved_quantity)
    try:
        item = await crud.create(db, data)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Product already stocked in this warehouse")
    await invalidate_inventory_summary(redis)
    return item


@router.patch(
    "/{item_id}",
    response_model=InventoryItemResponse,
    dependencies=[Depends(require_roles(ALL_ROLES))],
)
async def update_stock(
    item_id: uuid.UUID,
    data: InventoryItemUpdate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> InventoryItem:
    item = await crud.get(db, item_id, for_update=True)  # row lock: serialise concurrent count updates
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inventory item not found")
    on_hand = data.quantity_on_hand if data.quantity_on_hand is not None else item.quantity_on_hand
    reserved = data.reserved_quantity if data.reserved_quantity is not None else item.reserved_quantity
    if reserved > on_hand:
        await db.rollback()  # release the row lock
        _check_quantities(on_hand, reserved)
    try:
        item = await crud.update(db, item, data)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Invalid stock quantities")
    await invalidate_inventory_summary(redis)
    return item


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles([Role.ADMIN]))],
)
async def delete_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    item = await crud.get(db, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inventory item not found")
    await crud.delete(db, item)
    await invalidate_inventory_summary(redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
