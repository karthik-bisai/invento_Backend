import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_redis, require_roles
from app.crud import warehouse as crud
from app.crud.base import ConflictError
from app.models.user import Role
from app.models.warehouse import Warehouse
from app.schemas.common import Page, PageParams
from app.schemas.warehouse import WarehouseCreate, WarehouseResponse, WarehouseUpdate
from app.services.inventory_summary import invalidate_inventory_summary

router = APIRouter(prefix="/warehouses", tags=["warehouses"])


async def _get_or_404(db: AsyncSession, warehouse_id: uuid.UUID) -> Warehouse:
    warehouse = await crud.get(db, warehouse_id)
    if warehouse is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
    return warehouse


@router.get("", response_model=Page[WarehouseResponse], dependencies=[Depends(get_current_user)])
async def list_warehouses(
    params: PageParams = Depends(),
    q: str | None = Query(None, description="Search name or location"),
    db: AsyncSession = Depends(get_db),
) -> Page[WarehouseResponse]:
    items, total = await crud.list_warehouses(db, params.page, params.size, q)
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.get("/{warehouse_id}", response_model=WarehouseResponse, dependencies=[Depends(get_current_user)])
async def get_warehouse(warehouse_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Warehouse:
    return await _get_or_404(db, warehouse_id)


@router.post(
    "",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles([Role.ADMIN, Role.INVENTORY_MANAGER]))],
)
async def create_warehouse(
    data: WarehouseCreate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Warehouse:
    try:
        warehouse = await crud.create(db, data)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Warehouse name already exists")
    await invalidate_inventory_summary(redis)  # warehouse count / breakdown changed
    return warehouse


@router.patch(
    "/{warehouse_id}",
    response_model=WarehouseResponse,
    dependencies=[Depends(require_roles([Role.ADMIN]))],
)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    data: WarehouseUpdate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Warehouse:
    try:
        warehouse = await crud.update(db, await _get_or_404(db, warehouse_id), data)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Warehouse name already exists")
    await invalidate_inventory_summary(redis)
    return warehouse


@router.delete(
    "/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles([Role.ADMIN]))],
)
async def delete_warehouse(
    warehouse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    try:
        await crud.delete(db, await _get_or_404(db, warehouse_id))
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Warehouse still has inventory records")
    await invalidate_inventory_summary(redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
