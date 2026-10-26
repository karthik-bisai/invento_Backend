import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import paginate, remove, save
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate


async def get(db: AsyncSession, product_id: uuid.UUID) -> Product | None:
    return await db.get(Product, product_id)


async def list_products(
    db: AsyncSession, page: int, size: int, category: str | None = None, q: str | None = None
) -> tuple[list[Product], int]:
    stmt = select(Product).order_by(Product.name, Product.id)
    if category:
        stmt = stmt.where(Product.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    return await paginate(db, stmt, page, size)


async def create(db: AsyncSession, data: ProductCreate) -> Product:
    product = Product(**data.model_dump())
    await save(db, product)
    return product


async def update(db: AsyncSession, product: Product, data: ProductUpdate) -> Product:
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(product, field, value)
    await save(db, product)
    return product


async def set_reorder_level(db: AsyncSession, product: Product, reorder_level: int) -> Product:
    product.reorder_level = reorder_level
    await save(db, product)
    return product


async def delete(db: AsyncSession, product: Product) -> None:
    await remove(db, product)
