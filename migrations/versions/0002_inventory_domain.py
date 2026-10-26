"""products, warehouses, inventory_items

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("reorder_level", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("unit_price >= 0", name="ck_products_unit_price_nonneg"),
        sa.CheckConstraint("reorder_level >= 0", name="ck_products_reorder_level_nonneg"),
    )
    op.create_index("ix_products_sku", "products", ["sku"], unique=True)
    op.create_index("ix_products_name", "products", ["name"])
    op.create_index("ix_products_category", "products", ["category"])

    op.create_table(
        "warehouses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("location", sa.String(255), nullable=False),
    )

    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("product_id", "warehouse_id", name="uq_inventory_product_warehouse"),
        sa.CheckConstraint("quantity_on_hand >= 0", name="ck_inventory_on_hand_nonneg"),
        sa.CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_nonneg"),
        sa.CheckConstraint("reserved_quantity <= quantity_on_hand", name="ck_inventory_reserved_lte_on_hand"),
    )
    op.create_index("ix_inventory_items_product_id", "inventory_items", ["product_id"])
    op.create_index("ix_inventory_items_warehouse_id", "inventory_items", ["warehouse_id"])


def downgrade() -> None:
    op.drop_table("inventory_items")
    op.drop_table("warehouses")
    op.drop_table("products")
