import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_redis, require_roles
from app.crud import product as crud
from app.crud.base import ConflictError
from app.models.product import Product
from app.models.user import Role
from app.schemas.common import Page, PageParams
from app.schemas.product import ProductCreate, ProductResponse, ProductUpdate, ReorderLevelUpdate
from app.services.inventory_summary import invalidate_inventory_summary

router = APIRouter(prefix="/products", tags=["products"])

MANAGERS = [Role.ADMIN, Role.INVENTORY_MANAGER]


async def _get_or_404(db: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await crud.get(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.get("", response_model=Page[ProductResponse], dependencies=[Depends(get_current_user)])
async def list_products(
    params: PageParams = Depends(),
    category: str | None = Query(None),
    q: str | None = Query(None, description="Search name or SKU"),
    db: AsyncSession = Depends(get_db),
) -> Page[ProductResponse]:
    items, total = await crud.list_products(db, params.page, params.size, category, q)
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.get("/{product_id}", response_model=ProductResponse, dependencies=[Depends(get_current_user)])
async def get_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Product:
    return await _get_or_404(db, product_id)


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(MANAGERS))],
)
async def create_product(data: ProductCreate, db: AsyncSession = Depends(get_db)) -> Product:
    try:
        return await crud.create(db, data)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="SKU already exists")


@router.patch(
    "/{product_id}/reorder-level",
    response_model=ProductResponse,
    dependencies=[Depends(require_roles(MANAGERS))],
)
async def set_reorder_level(
    product_id: uuid.UUID,
    data: ReorderLevelUpdate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Product:
    product = await crud.set_reorder_level(db, await _get_or_404(db, product_id), data.reorder_level)
    await invalidate_inventory_summary(redis)  # low-stock count depends on reorder levels
    return product


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_roles([Role.ADMIN]))],
)
async def update_product(
    product_id: uuid.UUID,
    data: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Product:
    try:
        product = await crud.update(db, await _get_or_404(db, product_id), data)
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="SKU already exists")
    await invalidate_inventory_summary(redis)  # price/reorder level feed the summary
    return product


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles([Role.ADMIN]))],
)
async def delete_product(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    try:
        await crud.delete(db, await _get_or_404(db, product_id))
    except ConflictError:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Product still has inventory records")
    await invalidate_inventory_summary(redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
