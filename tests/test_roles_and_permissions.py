import uuid
import pytest
from httpx import AsyncClient

from app.core.security import create_access_token
from app.crud import organization as crud_org
from app.crud import user as crud_user
from app.models.user import Role

API = "/api/v1"


@pytest.fixture
async def role_org_setup(session_factory):
    async with session_factory() as db:
        org_a = await crud_org.create(db, name="Alpha Corp", location="Berlin", currency="EUR")
        org_b = await crud_org.create(db, name="Beta Ltd", location="Tokyo", currency="USD")
        await db.commit()

        admin_a = await crud_user.create(
            db,
            type("UserIn", (), {"email": "admin@alpha.com", "password": "pw", "full_name": "Admin Alpha", "role": Role.ADMIN})(),
            organization_id=org_a.id,
            role=Role.ADMIN,
            is_active=True,
            email_verified=True,
        )
        staff_a = await crud_user.create(
            db,
            type("UserIn", (), {"email": "staff@alpha.com", "password": "pw", "full_name": "Staff Alpha", "role": Role.WAREHOUSE_STAFF})(),
            organization_id=org_a.id,
            role=Role.WAREHOUSE_STAFF,
            is_active=True,
            email_verified=True,
        )
        admin_b = await crud_user.create(
            db,
            type("UserIn", (), {"email": "admin@beta.com", "password": "pw", "full_name": "Admin Beta", "role": Role.ADMIN})(),
            organization_id=org_b.id,
            role=Role.ADMIN,
            is_active=True,
            email_verified=True,
        )
        return {
            "org_a": org_a,
            "org_b": org_b,
            "admin_a": admin_a,
            "staff_a": staff_a,
            "admin_b": admin_b,
            "admin_a_header": {"Authorization": f"Bearer {create_access_token(str(admin_a.id), Role.ADMIN.value)}"},
            "staff_a_header": {"Authorization": f"Bearer {create_access_token(str(staff_a.id), Role.WAREHOUSE_STAFF.value)}"},
            "admin_b_header": {"Authorization": f"Bearer {create_access_token(str(admin_b.id), Role.ADMIN.value)}"},
        }


async def test_permissions_catalog(client: AsyncClient, role_org_setup):
    res = await client.get(f"{API}/roles/permissions-catalog", headers=role_org_setup["staff_a_header"])
    assert res.status_code == 200
    catalog = res.json()
    modules = [item["module"] for item in catalog]
    assert "products" in modules
    assert "inventory" in modules
    assert "warehouses" in modules
    assert "users" in modules
    assert "settings" in modules


async def test_list_organization_roles(client: AsyncClient, role_org_setup):
    res = await client.get(f"{API}/roles", headers=role_org_setup["admin_a_header"])
    assert res.status_code == 200
    roles = res.json()
    assert len(roles) >= 3
    names = [r["name"] for r in roles]
    assert "Admin" in names
    assert "Inventory Manager" in names
    assert "Warehouse Staff" in names

    # Check user counts
    staff_role = next(r for r in roles if r["name"] == "Warehouse Staff")
    assert staff_role["user_count"] >= 1


async def test_non_admin_cannot_list_or_create_roles(client: AsyncClient, role_org_setup):
    res = await client.get(f"{API}/roles", headers=role_org_setup["staff_a_header"])
    assert res.status_code == 403

    res_create = await client.post(
        f"{API}/roles",
        headers=role_org_setup["staff_a_header"],
        json={"name": "Hacker Role", "permissions": []},
    )
    assert res_create.status_code == 403


async def test_admin_create_custom_role(client: AsyncClient, role_org_setup):
    payload = {
        "name": "Procurement Specialist",
        "description": "Handles purchase orders and supplier coordination",
        "permissions": ["products:view", "inventory:view", "reports:view"],
    }
    res = await client.post(f"{API}/roles", headers=role_org_setup["admin_a_header"], json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Procurement Specialist"
    assert data["description"] == payload["description"]
    assert "products:view" in data["permissions"]
    assert data["is_system"] is False


async def test_admin_update_role_permissions(client: AsyncClient, role_org_setup):
    # Create role
    create_res = await client.post(
        f"{API}/roles",
        headers=role_org_setup["admin_a_header"],
        json={"name": "Auditor", "permissions": ["dashboard:view", "reports:view"]},
    )
    role_id = create_res.json()["id"]

    # Update permissions
    update_res = await client.patch(
        f"{API}/roles/{role_id}",
        headers=role_org_setup["admin_a_header"],
        json={"permissions": ["dashboard:view", "reports:view", "products:view", "warehouses:view"]},
    )
    assert update_res.status_code == 200
    updated_perms = update_res.json()["permissions"]
    assert "products:view" in updated_perms
    assert "warehouses:view" in updated_perms


async def test_system_role_protection(client: AsyncClient, role_org_setup):
    roles = (await client.get(f"{API}/roles", headers=role_org_setup["admin_a_header"])).json()
    admin_role = next(r for r in roles if r["name"] == "Admin")

    # Cannot delete system admin role
    del_res = await client.delete(f"{API}/roles/{admin_role['id']}", headers=role_org_setup["admin_a_header"])
    assert del_res.status_code == 409
    assert "System roles cannot be deleted" in del_res.json()["detail"]


async def test_cross_org_role_isolation(client: AsyncClient, role_org_setup):
    roles_a = (await client.get(f"{API}/roles", headers=role_org_setup["admin_a_header"])).json()
    role_a_id = roles_a[0]["id"]

    # Admin B cannot view, edit, or delete Org A's role
    assert (await client.get(f"{API}/roles/{role_a_id}", headers=role_org_setup["admin_b_header"])).status_code == 404
    assert (await client.patch(f"{API}/roles/{role_a_id}", headers=role_org_setup["admin_b_header"], json={"name": "Hijacked"})).status_code == 404
    assert (await client.delete(f"{API}/roles/{role_a_id}", headers=role_org_setup["admin_b_header"])).status_code == 404


async def test_effective_permissions_in_me(client: AsyncClient, role_org_setup):
    # Admin A me has all permissions
    me_admin = (await client.get(f"{API}/auth/me", headers=role_org_setup["admin_a_header"])).json()
    assert "products:create" in me_admin["permissions"]
    assert "roles:edit" in me_admin["permissions"]

    # Staff A me has warehouse staff permissions
    me_staff = (await client.get(f"{API}/auth/me", headers=role_org_setup["staff_a_header"])).json()
    assert "inventory:view" in me_staff["permissions"]
    assert "roles:edit" not in me_staff["permissions"]
