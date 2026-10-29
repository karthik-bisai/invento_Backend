import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_roles
from app.crud import role as crud_role
from app.crud.base import ConflictError
from app.models.user import Role, User
from app.schemas.role import PermissionCatalogItem, RoleCreate, RoleResponse, RoleUpdate

router = APIRouter(prefix="/roles", tags=["roles"])

ADMIN_OR_ROLES_VIEW = require_roles([Role.ADMIN])
ADMIN_OR_ROLES_EDIT = require_roles([Role.ADMIN])


@router.get("/permissions-catalog", response_model=list[PermissionCatalogItem])
async def get_permissions_catalog(
    user: User = Depends(get_current_user),
) -> list[PermissionCatalogItem]:
    """Return the application modules and supported actions for permission configuration."""
    return crud_role.PERMISSION_CATALOG


@router.get("", response_model=list[RoleResponse])
async def list_roles(
    user: User = Depends(ADMIN_OR_ROLES_VIEW),
    db: AsyncSession = Depends(get_db),
) -> list[RoleResponse]:
    """List all roles configured for the administrator's organization."""
    if user.organization_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No organization associated with user")
    pairs = await crud_role.list_roles(db, user.organization_id)
    return [
        RoleResponse(
            id=r.id,
            organization_id=r.organization_id,
            name=r.name,
            description=r.description,
            is_system=r.is_system,
            permissions=r.permissions,
            user_count=count,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r, count in pairs
    ]


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    data: RoleCreate,
    user: User = Depends(ADMIN_OR_ROLES_EDIT),
    db: AsyncSession = Depends(get_db),
) -> RoleResponse:
    """Create a new role for the current organization."""
    if user.organization_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No organization associated with user")
    try:
        role = await crud_role.create_role(db, user.organization_id, data)
        return RoleResponse(
            id=role.id,
            organization_id=role.organization_id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permissions=role.permissions,
            user_count=0,
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
    except ConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: uuid.UUID,
    user: User = Depends(ADMIN_OR_ROLES_VIEW),
    db: AsyncSession = Depends(get_db),
) -> RoleResponse:
    role = await crud_role.get_role(db, role_id, organization_id=user.organization_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Role not found")
    return RoleResponse.model_validate(role)


@router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: uuid.UUID,
    data: RoleUpdate,
    user: User = Depends(ADMIN_OR_ROLES_EDIT),
    db: AsyncSession = Depends(get_db),
) -> RoleResponse:
    """Update role details and permissions."""
    role = await crud_role.get_role(db, role_id, organization_id=user.organization_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Role not found")
    try:
        updated = await crud_role.update_role(db, role, data)
        return RoleResponse.model_validate(updated)
    except ConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    user: User = Depends(ADMIN_OR_ROLES_EDIT),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a custom role. System roles and roles with assigned users cannot be deleted."""
    role = await crud_role.get_role(db, role_id, organization_id=user.organization_id)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Role not found")
    try:
        await crud_role.delete_role(db, role)
    except ConflictError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
