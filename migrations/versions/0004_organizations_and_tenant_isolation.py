"""organizations, org_code, currency and tenant isolation

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create organizations table
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_code", sa.String(16), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_organizations_org_code", "organizations", ["org_code"], unique=True)

    # Add organization_id and department to users
    op.add_column("users", sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True))
    op.add_column("users", sa.Column("department", sa.String(120), nullable=True))
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    # Add organization_id to access_requests
    op.add_column("access_requests", sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_access_requests_organization_id", "access_requests", ["organization_id"])

    # Add organization_id to warehouses
    op.add_column("warehouses", sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_warehouses_organization_id", "warehouses", ["organization_id"])
    op.execute("ALTER TABLE warehouses DROP CONSTRAINT IF EXISTS warehouses_name_key")
    op.execute("DROP INDEX IF EXISTS ix_warehouses_name")
    op.create_unique_constraint("uq_warehouses_org_name", "warehouses", ["organization_id", "name"])

    # Add organization_id to products
    op.add_column("products", sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_products_organization_id", "products", ["organization_id"])
    op.execute("DROP INDEX IF EXISTS ix_products_sku")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_sku_key")
    op.create_unique_constraint("uq_products_org_sku", "products", ["organization_id", "sku"])

    # Add organization_id and dismissed_at to notifications
    op.add_column("notifications", sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True))
    op.add_column("notifications", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_notifications_organization_id", "notifications", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_organization_id", table_name="notifications")
    op.drop_column("notifications", "dismissed_at")
    op.drop_column("notifications", "organization_id")

    op.drop_constraint("uq_products_org_sku", "products", type_="unique")
    op.create_unique_constraint("products_sku_key", "products", ["sku"])
    op.drop_index("ix_products_organization_id", table_name="products")
    op.drop_column("products", "organization_id")

    op.drop_constraint("uq_warehouses_org_name", "warehouses", type_="unique")
    op.create_unique_constraint("warehouses_name_key", "warehouses", ["name"])
    op.drop_index("ix_warehouses_organization_id", table_name="warehouses")
    op.drop_column("warehouses", "organization_id")

    op.drop_index("ix_access_requests_organization_id", table_name="access_requests")
    op.drop_column("access_requests", "organization_id")

    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_column("users", "department")
    op.drop_column("users", "organization_id")

    op.drop_index("ix_organizations_org_code", table_name="organizations")
    op.drop_table("organizations")
