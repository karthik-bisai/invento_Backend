"""profiles, email verification, access requests, notifications

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

request_status = sa.Enum("PENDING", "APPROVED", "REJECTED", name="access_request_status")


def upgrade() -> None:
    # Existing accounts predate email verification: treat them as verified.
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("users", "email_verified", server_default=sa.false())
    op.add_column("users", sa.Column("full_name", sa.String(120)))
    op.add_column("users", sa.Column("job_title", sa.String(120)))
    op.add_column("users", sa.Column("phone", sa.String(32)))
    op.add_column("users", sa.Column("avatar_url", sa.String(500)))
    op.add_column("users", sa.Column("preferences", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("users", sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))

    op.create_table(
        "access_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", request_status, nullable=False, server_default="PENDING"),
        sa.Column("decided_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_access_requests_status", "access_requests", ["status"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(500)),
        sa.Column("link", sa.String(200)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    # Accounts that were already waiting for approval become pending access requests.
    op.execute(
        "INSERT INTO access_requests (id, user_id, status, created_at) "
        "SELECT gen_random_uuid(), id, 'PENDING', now() FROM users WHERE is_active = false"
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_access_requests_status", table_name="access_requests")
    op.drop_table("access_requests")
    request_status.drop(op.get_bind(), checkfirst=True)
    for col in ("created_at", "preferences", "avatar_url", "phone", "job_title", "full_name", "email_verified"):
        op.drop_column("users", col)
