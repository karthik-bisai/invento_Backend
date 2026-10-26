import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"
    WAREHOUSE_STAFF = "WAREHOUSE_STAFF"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="user_role"),
        default=Role.WAREHOUSE_STAFF,
        server_default=Role.WAREHOUSE_STAFF.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # False until the person proves they own the address (OTP); admin-created accounts start verified.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    full_name: Mapped[str | None] = mapped_column(String(120), default=None)
    job_title: Mapped[str | None] = mapped_column(String(120), default=None)
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    avatar_url: Mapped[str | None] = mapped_column(String(500), default=None)
    # Validated through schemas.user.Preferences on every read and write; unknown keys are dropped.
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
