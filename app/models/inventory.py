import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", name="uq_inventory_product_warehouse"),
        CheckConstraint("quantity_on_hand >= 0", name="ck_inventory_on_hand_nonneg"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_nonneg"),
        CheckConstraint("reserved_quantity <= quantity_on_hand", name="ck_inventory_reserved_lte_on_hand"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), index=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
