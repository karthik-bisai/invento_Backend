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


async def _get_or_404(db: AsyncSession, warehouse_id: uuid.UUID, organization_id: uuid.UUID | None = None) -> Warehouse:
    warehouse = await crud.get(db, warehouse_id)
    if warehouse is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
    if organization_id is not None and warehouse.organization_id is not None and warehouse.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Warehouse not found")
    return warehouse


@router.get("", response_model=Page[WarehouseResponse])
async def list_warehouses(
    params: PageParams = Depends(),
    q: str | None = Query(None, description="Search name or location"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[WarehouseResponse]:
    items, total = await crud.list_warehouses(db, params.page, params.size, q, organization_id=user.organization_id)
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
async def get_warehouse(
    warehouse_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Warehouse:
    return await _get_or_404(db, warehouse_id, organization_id=user.organization_id)


@router.post(
    "",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_warehouse(
    data: WarehouseCreate,
    user: User = Depends(require_roles([Role.ADMIN, Role.INVENTORY_MANAGER])),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Warehouse:
    try:
        warehouse = await crud.create(db, data, organization_id=user.organization_id)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Warehouse name already exists")
    await invalidate_inventory_summary(redis, organization_id=user.organization_id)  # warehouse count / breakdown changed
    return warehouse


@router.patch(
    "/{warehouse_id}",
    response_model=WarehouseResponse,
)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    data: WarehouseUpdate,
    user: User = Depends(require_roles([Role.ADMIN])),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Warehouse:
    try:
        warehouse = await crud.update(db, await _get_or_404(db, warehouse_id, organization_id=user.organization_id), data)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Warehouse name already exists")
    await invalidate_inventory_summary(redis, organization_id=user.organization_id)
    return warehouse


@router.delete(
    "/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_warehouse(
    warehouse_id: uuid.UUID,
    user: User = Depends(require_roles([Role.ADMIN])),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    try:
        await crud.delete(db, await _get_or_404(db, warehouse_id, organization_id=user.organization_id))
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Warehouse still has inventory records")
    await invalidate_inventory_summary(redis, organization_id=user.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
