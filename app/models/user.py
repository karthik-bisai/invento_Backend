import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.organization import Organization


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"
    WAREHOUSE_STAFF = "WAREHOUSE_STAFF"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True, index=True, default=None
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_roles.id", ondelete="SET NULL"), nullable=True, index=True, default=None
    )
    role: Mapped[str] = mapped_column(
        String(64),
        default=Role.WAREHOUSE_STAFF.value,
        server_default=Role.WAREHOUSE_STAFF.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # False until the person proves they own the address (OTP); admin-created accounts start verified.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    full_name: Mapped[str | None] = mapped_column(String(120), default=None)
    job_title: Mapped[str | None] = mapped_column(String(120), default=None)
    department: Mapped[str | None] = mapped_column(String(120), default=None)
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    avatar_url: Mapped[str | None] = mapped_column(String(500), default=None)
    # Validated through schemas.user.Preferences on every read and write; unknown keys are dropped.
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    organization: Mapped["Organization | None"] = relationship("Organization", back_populates="users", foreign_keys=[organization_id], lazy="selectin")

    @property
    def org_code(self) -> str | None:
        return self.organization.org_code if self.organization else None

    @property
    def organization_name(self) -> str | None:
        return self.organization.name if self.organization else None

    @property
    def organization_currency(self) -> str:
        return self.organization.currency if self.organization else "USD"

