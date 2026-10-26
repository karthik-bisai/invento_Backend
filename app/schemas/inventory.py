import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class InventoryItemCreate(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity_on_hand: int = Field(default=0, ge=0)
    reserved_quantity: int = Field(default=0, ge=0)


class InventoryItemUpdate(BaseModel):
    quantity_on_hand: int | None = Field(default=None, ge=0)
    reserved_quantity: int | None = Field(default=None, ge=0)


class InventoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity_on_hand: int
    reserved_quantity: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def available_quantity(self) -> int:
        return self.quantity_on_hand - self.reserved_quantity


class InventoryRow(InventoryItemResponse):
    """Inventory item joined with product and warehouse details, for the list view."""

    sku: str
    product_name: str
    category: str
    unit_price: Decimal
    reorder_level: int
    warehouse_name: str
    product_total_quantity: int  # on hand across all warehouses; drives the stock status


class LowStockItem(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    quantity_on_hand: int
    reorder_level: int
    status: Literal["low", "critical"]


class WarehouseSummary(BaseModel):
    warehouse_id: uuid.UUID
    name: str
    quantity_on_hand: int
    reserved_quantity: int
    total_value: Decimal


class InventorySummary(BaseModel):
    total_products: int
    total_warehouses: int
    total_quantity_on_hand: int
    total_reserved_quantity: int
    total_available_quantity: int
    total_value: Decimal
    low_stock_products: int
    warehouses: list[WarehouseSummary]
