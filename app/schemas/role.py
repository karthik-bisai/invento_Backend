import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None = None
    is_system: bool = False
    permissions: list[str] = Field(default_factory=list)
    user_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PermissionAction(BaseModel):
    action: str
    label: str


class PermissionCatalogItem(BaseModel):
    module: str
    label: str
    actions: list[PermissionAction]
