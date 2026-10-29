import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import ConflictError, remove, save
from app.models.role import OrganizationRole
from app.models.user import Role, User
from app.schemas.role import PermissionAction, PermissionCatalogItem, RoleCreate, RoleUpdate

PERMISSION_CATALOG: list[PermissionCatalogItem] = [
    PermissionCatalogItem(
        module="dashboard",
        label="Dashboard & Overview",
        actions=[
            PermissionAction(action="view", label="View"),
        ],
    ),
    PermissionCatalogItem(
        module="products",
        label="Products",
        actions=[
            PermissionAction(action="view", label="View"),
            PermissionAction(action="create", label="Create"),
            PermissionAction(action="edit", label="Edit"),
            PermissionAction(action="delete", label="Delete"),
        ],
    ),
    PermissionCatalogItem(
        module="inventory",
        label="Inventory",
        actions=[
            PermissionAction(action="view", label="View"),
            PermissionAction(action="create", label="Create"),
            PermissionAction(action="edit", label="Edit"),
            PermissionAction(action="delete", label="Delete"),
        ],
    ),
    PermissionCatalogItem(
        module="warehouses",
        label="Warehouses",
        actions=[
            PermissionAction(action="view", label="View"),
            PermissionAction(action="create", label="Create"),
            PermissionAction(action="edit", label="Edit"),
            PermissionAction(action="delete", label="Delete"),
        ],
    ),
    PermissionCatalogItem(
        module="reports",
        label="Reports",
        actions=[
            PermissionAction(action="view", label="View"),
        ],
    ),
    PermissionCatalogItem(
        module="users",
        label="Employees & Users",
        actions=[
            PermissionAction(action="view", label="View"),
            PermissionAction(action="create", label="Create"),
            PermissionAction(action="edit", label="Edit"),
            PermissionAction(action="delete", label="Delete"),
            PermissionAction(action="approve", label="Approve Requests"),
        ],
    ),
    PermissionCatalogItem(
        module="settings",
        label="Organization Settings",
        actions=[
            PermissionAction(action="view", label="View"),
            PermissionAction(action="edit", label="Edit"),
        ],
    ),
    PermissionCatalogItem(
        module="roles",
        label="Roles & Permissions",
        actions=[
            PermissionAction(action="view", label="View"),
            PermissionAction(action="edit", label="Edit"),
        ],
    ),
]

ALL_PERMISSIONS: list[str] = [
    f"{item.module}:{action.action}"
    for item in PERMISSION_CATALOG
    for action in item.actions
]

DEFAULT_ROLES_CONFIG = [
    {
        "name": "Admin",
        "description": "Full administrative access to all modules, users, and organization settings.",
        "is_system": True,
        "permissions": ALL_PERMISSIONS,
    },
    {
        "name": "Inventory Manager",
        "description": "Can manage products, inventory stock counts, reorder levels, warehouses, and reports.",
        "is_system": False,
        "permissions": [
            "dashboard:view",
            "products:view",
            "products:create",
            "products:edit",
            "inventory:view",
            "inventory:create",
            "inventory:edit",
            "warehouses:view",
            "reports:view",
        ],
    },
    {
        "name": "Warehouse Staff",
        "description": "Can view products, view inventory, and update on-hand stock counts.",
        "is_system": False,
        "permissions": [
            "dashboard:view",
            "products:view",
            "inventory:view",
            "inventory:edit",
            "warehouses:view",
        ],
    },
    {
        "name": "Warehouse Manager",
        "description": "Manages warehouse stock, warehouse locations, and inventory transfers.",
        "is_system": False,
        "permissions": [
            "dashboard:view",
            "products:view",
            "products:create",
            "products:edit",
            "inventory:view",
            "inventory:create",
            "inventory:edit",
            "warehouses:view",
            "warehouses:create",
            "warehouses:edit",
            "reports:view",
        ],
    },
]


async def ensure_organization_roles(db: AsyncSession, organization_id: uuid.UUID) -> list[OrganizationRole]:
    """Ensure default roles exist for an organization."""
    existing = (
        await db.scalars(
            select(OrganizationRole).where(OrganizationRole.organization_id == organization_id)
        )
    ).all()
    if existing:
        return list(existing)

    created_roles: list[OrganizationRole] = []
    for cfg in DEFAULT_ROLES_CONFIG:
        role = OrganizationRole(
            organization_id=organization_id,
            name=cfg["name"],
            description=cfg["description"],
            is_system=cfg["is_system"],
            permissions=cfg["permissions"],
        )
        db.add(role)
        created_roles.append(role)
    await db.commit()
    return created_roles


async def list_roles(db: AsyncSession, organization_id: uuid.UUID) -> list[tuple[OrganizationRole, int]]:
    """List all roles for the organization with assigned user counts."""
    await ensure_organization_roles(db, organization_id)
    roles = (
        await db.scalars(
            select(OrganizationRole)
            .where(OrganizationRole.organization_id == organization_id)
            .order_by(OrganizationRole.is_system.desc(), OrganizationRole.name.asc())
        )
    ).all()

    counts_by_name = dict(
        (
            await db.execute(
                select(func.lower(User.role), func.count(User.id))
                .where(User.organization_id == organization_id)
                .group_by(func.lower(User.role))
            )
        ).all()
    )

    result: list[tuple[OrganizationRole, int]] = []
    for r in roles:
        # Match by name or standard enum mapping
        count = counts_by_name.get(r.name.lower(), 0)
        # Also check enum equivalent e.g. "Inventory Manager" -> "inventory_manager"
        key_enum = r.name.upper().replace(" ", "_").lower()
        if key_enum != r.name.lower() and key_enum in counts_by_name:
            count += counts_by_name[key_enum]
        result.append((r, count))
    return result


async def get_role(
    db: AsyncSession, role_id: uuid.UUID, organization_id: uuid.UUID | None = None
) -> OrganizationRole | None:
    role = await db.get(OrganizationRole, role_id)
    if role is None:
        return None
    if organization_id is not None and role.organization_id != organization_id:
        return None
    return role


async def get_role_by_name(
    db: AsyncSession, organization_id: uuid.UUID, name: str
) -> OrganizationRole | None:
    return await db.scalar(
        select(OrganizationRole).where(
            OrganizationRole.organization_id == organization_id,
            or_(
                func.lower(OrganizationRole.name) == name.lower(),
                func.lower(OrganizationRole.name) == name.replace("_", " ").lower(),
            ),
        )
    )


async def create_role(db: AsyncSession, organization_id: uuid.UUID, data: RoleCreate) -> OrganizationRole:
    trimmed_name = data.name.strip()
    existing = await db.scalar(
        select(OrganizationRole).where(
            OrganizationRole.organization_id == organization_id,
            func.lower(OrganizationRole.name) == trimmed_name.lower(),
        )
    )
    if existing:
        raise ConflictError(f"A role named '{trimmed_name}' already exists in this organization")

    role = OrganizationRole(
        organization_id=organization_id,
        name=trimmed_name,
        description=(data.description or "").strip() or None,
        is_system=False,
        permissions=data.permissions or [],
    )
    await save(db, role)
    return role


async def update_role(db: AsyncSession, role: OrganizationRole, data: RoleUpdate) -> OrganizationRole:
    dump = data.model_dump(exclude_unset=True)
    if "name" in dump and dump["name"]:
        trimmed_name = dump["name"].strip()
        if trimmed_name.lower() != role.name.lower():
            if role.is_system:
                raise ConflictError("Cannot rename system roles")
            existing = await db.scalar(
                select(OrganizationRole).where(
                    OrganizationRole.organization_id == role.organization_id,
                    OrganizationRole.id != role.id,
                    func.lower(OrganizationRole.name) == trimmed_name.lower(),
                )
            )
            if existing:
                raise ConflictError(f"A role named '{trimmed_name}' already exists in this organization")
            role.name = trimmed_name

    if "description" in dump:
        role.description = (dump["description"] or "").strip() or None

    if "permissions" in dump and dump["permissions"] is not None:
        perms = dump["permissions"]
        if role.is_system and "roles:edit" not in perms:
            # Safeguard: prevent disabling role/permission management from system admin role
            perms.append("roles:edit")
            perms.append("roles:view")
        role.permissions = list(set(perms))

    await save(db, role)
    return role


async def delete_role(db: AsyncSession, role: OrganizationRole) -> None:
    if role.is_system:
        raise ConflictError("System roles cannot be deleted")

    # Check if any user in the organization currently has this role
    norm_name = role.name.lower()
    enum_name = role.name.upper().replace(" ", "_").lower()
    in_use = await db.scalar(
        select(func.count(User.id)).where(
            User.organization_id == role.organization_id,
            or_(
                func.lower(User.role) == norm_name,
                func.lower(User.role) == enum_name,
            ),
        )
    )
    if in_use and in_use > 0:
        raise ConflictError(f"Cannot delete role '{role.name}' because {in_use} user(s) are assigned to it")

    await remove(db, role)


async def get_effective_permissions(db: AsyncSession, user: User) -> list[str]:
    """Return effective permissions for a user based on their organization role."""
    role_str = str(user.role.value if isinstance(user.role, Role) else user.role).upper()
    if role_str == "ADMIN":
        return list(ALL_PERMISSIONS)

    if user.organization_id:
        # Check database role
        role = await get_role_by_name(db, user.organization_id, role_str)
        if role:
            return list(role.permissions)

    # Built-in fallback
    for cfg in DEFAULT_ROLES_CONFIG:
        if cfg["name"].upper().replace(" ", "_") == role_str or cfg["name"].upper() == role_str:
            return list(cfg["permissions"])
    return list(DEFAULT_ROLES_CONFIG[2]["permissions"])  # default to warehouse staff
