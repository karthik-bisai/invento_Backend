"""admin configurable organization roles and permissions

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create organization_roles table
    op.create_table(
        "organization_roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_organization_roles_organization_id", "organization_roles", ["organization_id"])
    op.create_index("ix_organization_roles_name", "organization_roles", ["name"])
    op.create_unique_constraint("uq_org_roles_org_name", "organization_roles", ["organization_id", "name"])

    # 2. Alter users.role to VARCHAR(64) and add role_id foreign key
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(64) USING role::text")
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'WAREHOUSE_STAFF'")
    op.add_column("users", sa.Column("role_id", sa.Uuid(), sa.ForeignKey("organization_roles.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_users_role_id", "users", ["role_id"])


def downgrade() -> None:
    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_column("users", "role_id")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE user_role USING role::user_role")
    op.drop_constraint("uq_org_roles_org_name", "organization_roles", type_="unique")
    op.drop_index("ix_organization_roles_name", table_name="organization_roles")
    op.drop_index("ix_organization_roles_organization_id", table_name="organization_roles")
    op.drop_table("organization_roles")
