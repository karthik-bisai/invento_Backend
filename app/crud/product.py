import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import ConflictError, paginate, remove, save
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate


async def get(db: AsyncSession, product_id: uuid.UUID) -> Product | None:
    return await db.get(Product, product_id)


async def list_products(
    db: AsyncSession,
    page: int,
    size: int,
    category: str | None = None,
    q: str | None = None,
    organization_id: uuid.UUID | None = None,
) -> tuple[list[Product], int]:
    stmt = select(Product).order_by(Product.name, Product.id)
    if organization_id:
        stmt = stmt.where(Product.organization_id == organization_id)
    if category:
        stmt = stmt.where(Product.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    return await paginate(db, stmt, page, size)


async def create(db: AsyncSession, data: ProductCreate, organization_id: uuid.UUID | None = None) -> Product:
    existing = await db.scalar(
        select(Product).where(
            Product.sku == data.sku,
            Product.organization_id == organization_id if organization_id is not None else Product.organization_id.is_(None),
        )
    )
    if existing:
        raise ConflictError("SKU already exists")
    product = Product(**data.model_dump(), organization_id=organization_id)
    await save(db, product)
    return product


async def update(db: AsyncSession, product: Product, data: ProductUpdate) -> Product:
    dump = data.model_dump(exclude_unset=True, exclude_none=True)
    if "sku" in dump and dump["sku"] != product.sku:
        existing = await db.scalar(
            select(Product).where(
                Product.sku == dump["sku"],
                Product.organization_id == product.organization_id if product.organization_id is not None else Product.organization_id.is_(None),
            )
        )
        if existing:
            raise ConflictError("SKU already exists")
    for field, value in dump.items():
        setattr(product, field, value)
    await save(db, product)
    return product


async def set_reorder_level(db: AsyncSession, product: Product, reorder_level: int) -> Product:
    product.reorder_level = reorder_level
    await save(db, product)
    return product


async def delete(db: AsyncSession, product: Product) -> None:
    await remove(db, product)
