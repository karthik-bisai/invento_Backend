import logging
from decimal import Decimal

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryItem
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.schemas.inventory import InventorySummary, LowStockItem, WarehouseSummary

logger = logging.getLogger(__name__)

SUMMARY_CACHE_KEY = "inventory:summary"
SUMMARY_TTL_SECONDS = 60


def _low_stock_query():
    """Products whose total on-hand across warehouses is at/below a (non-zero) reorder level."""
    qty = func.coalesce(func.sum(InventoryItem.quantity_on_hand), 0).label("qty")
    return (
        select(Product.id, Product.sku, Product.name, Product.reorder_level, qty)
        .select_from(Product)
        .outerjoin(InventoryItem, InventoryItem.product_id == Product.id)
        .where(Product.reorder_level > 0)
        .group_by(Product.id, Product.sku, Product.name, Product.reorder_level)
        .having(func.coalesce(func.sum(InventoryItem.quantity_on_hand), 0) <= Product.reorder_level)
    )


async def list_low_stock(db: AsyncSession, limit: int = 20) -> list[LowStockItem]:
    """Most depleted products first (lowest stock relative to reorder level)."""
    rows = (await db.execute(_low_stock_query().order_by(text("qty * 1.0 / reorder_level"), Product.name).limit(limit))).all()
    return [
        LowStockItem(
            product_id=r.id,
            sku=r.sku,
            name=r.name,
            quantity_on_hand=int(r.qty),
            reorder_level=r.reorder_level,
            status="critical" if r.qty * 2 <= r.reorder_level else "low",
        )
        for r in rows
    ]


async def compute_inventory_summary(db: AsyncSession) -> InventorySummary:
    total_products = await db.scalar(select(func.count(Product.id))) or 0
    total_warehouses = await db.scalar(select(func.count(Warehouse.id))) or 0

    on_hand = func.coalesce(func.sum(InventoryItem.quantity_on_hand), 0)
    reserved = func.coalesce(func.sum(InventoryItem.reserved_quantity), 0)
    value = func.coalesce(func.sum(InventoryItem.quantity_on_hand * Product.unit_price), 0)

    totals = (
        await db.execute(
            select(on_hand, reserved, value).select_from(InventoryItem).join(Product, Product.id == InventoryItem.product_id)
        )
    ).one()

    low_stock = await db.scalar(select(func.count()).select_from(_low_stock_query().subquery())) or 0

    wh_rows = (
        await db.execute(
            select(
                Warehouse.id,
                Warehouse.name,
                func.coalesce(func.sum(InventoryItem.quantity_on_hand), 0),
                func.coalesce(func.sum(InventoryItem.reserved_quantity), 0),
                func.coalesce(func.sum(InventoryItem.quantity_on_hand * Product.unit_price), 0),
            )
            .select_from(Warehouse)
            .outerjoin(InventoryItem, InventoryItem.warehouse_id == Warehouse.id)
            .outerjoin(Product, Product.id == InventoryItem.product_id)
            .group_by(Warehouse.id, Warehouse.name)
            .order_by(Warehouse.name)
        )
    ).all()

    return InventorySummary(
        total_products=total_products,
        total_warehouses=total_warehouses,
        total_quantity_on_hand=int(totals[0]),
        total_reserved_quantity=int(totals[1]),
        total_available_quantity=int(totals[0]) - int(totals[1]),
        total_value=Decimal(totals[2]).quantize(Decimal("0.01")),
        low_stock_products=low_stock,
        warehouses=[
            WarehouseSummary(
                warehouse_id=r[0],
                name=r[1],
                quantity_on_hand=int(r[2]),
                reserved_quantity=int(r[3]),
                total_value=Decimal(r[4]).quantize(Decimal("0.01")),
            )
            for r in wh_rows
        ],
    )


async def get_inventory_summary(db: AsyncSession, redis: Redis) -> InventorySummary:
    """Return the aggregate summary, served from Redis for up to 60s. Falls back to the DB if Redis is down."""
    try:
        cached = await redis.get(SUMMARY_CACHE_KEY)
        if cached:
            return InventorySummary.model_validate_json(cached)
    except RedisError:
        logger.warning("Redis read failed for %s", SUMMARY_CACHE_KEY, exc_info=True)

    summary = await compute_inventory_summary(db)

    try:
        await redis.set(SUMMARY_CACHE_KEY, summary.model_dump_json(), ex=SUMMARY_TTL_SECONDS)
    except RedisError:
        logger.warning("Redis write failed for %s", SUMMARY_CACHE_KEY, exc_info=True)
    return summary


async def invalidate_inventory_summary(redis: Redis) -> None:
    """Drop the cached summary. Call after any committed change that affects it."""
    try:
        await redis.delete(SUMMARY_CACHE_KEY)
    except RedisError:
        logger.warning("Redis delete failed for %s; summary may be stale up to %ss", SUMMARY_CACHE_KEY, SUMMARY_TTL_SECONDS, exc_info=True)
