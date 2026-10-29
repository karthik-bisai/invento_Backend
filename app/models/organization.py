import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.role import OrganizationRole


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    location: Mapped[str | None] = mapped_column(String(255), default=None)
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="organization")
    warehouses: Mapped[list["Warehouse"]] = relationship("Warehouse", back_populates="organization")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="organization")
    access_requests: Mapped[list["AccessRequest"]] = relationship("AccessRequest", back_populates="organization")
    notifications: Mapped[list["Notification"]] = relationship("Notification", back_populates="organization")
    roles: Mapped[list["OrganizationRole"]] = relationship("OrganizationRole", back_populates="organization", cascade="all, delete-orphan")

