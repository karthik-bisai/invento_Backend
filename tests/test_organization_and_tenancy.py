import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.crud import organization as crud_org
from app.crud import user as crud_user
from app.models.organization import Organization
from app.models.user import Role, User

API = "/api/v1"
PW_HASH = hash_password("Password123")


@pytest.fixture
async def two_orgs(session_factory):
    """Fixture creating two distinct organizations with admins and warehouse staff."""
    async with session_factory() as db:
        org_a = await crud_org.create(db, name="Acme Logistics", location="New York", currency="USD")
        org_b = await crud_org.create(db, name="Beta Distribution", location="London", currency="GBP")
        await db.commit()

        admin_a = await crud_user.create(
            db,
            type("UserIn", (), {"email": "admin_a@acme.com", "password": "pw", "full_name": "Admin A", "role": Role.ADMIN})(),
            organization_id=org_a.id,
            role=Role.ADMIN,
            is_active=True,
            email_verified=True,
        )
        staff_a = await crud_user.create(
            db,
            type("UserIn", (), {"email": "staff_a@acme.com", "password": "pw", "full_name": "Staff A", "role": Role.WAREHOUSE_STAFF})(),
            organization_id=org_a.id,
            role=Role.WAREHOUSE_STAFF,
            is_active=True,
            email_verified=True,
        )

        admin_b = await crud_user.create(
            db,
            type("UserIn", (), {"email": "admin_b@beta.com", "password": "pw", "full_name": "Admin B", "role": Role.ADMIN})(),
            organization_id=org_b.id,
            role=Role.ADMIN,
            is_active=True,
            email_verified=True,
        )

        return {
            "org_a": org_a,
            "org_b": org_b,
            "admin_a_header": {"Authorization": f"Bearer {create_access_token(str(admin_a.id), Role.ADMIN.value)}"},
            "staff_a_header": {"Authorization": f"Bearer {create_access_token(str(staff_a.id), Role.WAREHOUSE_STAFF.value)}"},
            "admin_b_header": {"Authorization": f"Bearer {create_access_token(str(admin_b.id), Role.ADMIN.value)}"},
            "admin_a": admin_a,
            "admin_b": admin_b,
            "staff_a": staff_a,
        }


async def test_org_code_generation_is_unique(session_factory):
    async with session_factory() as db:
        codes = set()
        for i in range(10):
            org = await crud_org.create(db, name=f"Test Org {i}")
            assert org.org_code.startswith("ORG-")
            assert len(org.org_code) >= 10
            assert org.org_code not in codes
            codes.add(org.org_code)


async def test_admin_registration_creates_org(client: AsyncClient):
    payload = {
        "email": "newadmin@corp.com",
        "password": "SecurePassword1!",
        "full_name": "New Corp Admin",
        "organization_name": "Corp Global",
        "organization_location": "San Francisco",
    }
    res = await client.post(f"{API}/auth/register/admin", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["email"] == "newadmin@corp.com"
    assert data["role"] == "ADMIN"
    assert data["is_active"] is True
    assert data["email_verified"] is False
    assert data["org_code"] is not None
    assert data["org_code"].startswith("ORG-")
    assert data["organization_name"] == "Corp Global"


async def test_employee_registration_requires_valid_org_code(client: AsyncClient, two_orgs):
    # Invalid org code
    bad_res = await client.post(
        f"{API}/auth/register/employee",
        json={"email": "emp@test.com", "password": "SecurePassword1!", "org_code": "ORG-INVALID99", "full_name": "Test Emp"},
    )
    assert bad_res.status_code == 404
    assert "Invalid Organization ID" in bad_res.json()["detail"]

    # Valid org code
    good_res = await client.post(
        f"{API}/auth/register/employee",
        json={"email": "emp@test.com", "password": "SecurePassword1!", "org_code": two_orgs["org_a"].org_code, "full_name": "Test Emp"},
    )
    assert good_res.status_code == 201
    data = good_res.json()
    assert data["email"] == "emp@test.com"
    assert data["role"] == "WAREHOUSE_STAFF"
    assert data["is_active"] is False  # awaits approval
    assert data["org_code"] == two_orgs["org_a"].org_code


async def test_organization_lookup_endpoint(client: AsyncClient, two_orgs):
    res = await client.get(f"{API}/organizations/lookup/{two_orgs['org_a'].org_code}")
    assert res.status_code == 200
    assert res.json() == {"exists": True, "name": "Acme Logistics", "org_code": two_orgs["org_a"].org_code}

    res_missing = await client.get(f"{API}/organizations/lookup/ORG-NONEXISTENT")
    assert res_missing.status_code == 200
    assert res_missing.json() == {"exists": False, "name": None, "org_code": None}


async def test_organization_currency_settings(client: AsyncClient, two_orgs):
    # Admin A reads organization settings
    me_res = await client.get(f"{API}/organizations/me", headers=two_orgs["admin_a_header"])
    assert me_res.status_code == 200
    assert me_res.json()["currency"] == "USD"
    assert me_res.json()["org_code"] == two_orgs["org_a"].org_code

    # Admin A updates currency to EUR
    patch_res = await client.patch(f"{API}/organizations/me", headers=two_orgs["admin_a_header"], json={"currency": "EUR"})
    assert patch_res.status_code == 200
    assert patch_res.json()["currency"] == "EUR"

    # Non-admin cannot change currency
    staff_res = await client.patch(f"{API}/organizations/me", headers=two_orgs["staff_a_header"], json={"currency": "INR"})
    assert staff_res.status_code == 403


async def test_multi_tenant_user_isolation(client: AsyncClient, two_orgs):
    # Admin A lists users: sees only users in Org A
    res_a = await client.get(f"{API}/auth/users", headers=two_orgs["admin_a_header"])
    assert res_a.status_code == 200
    emails_a = [u["email"] for u in res_a.json()["items"]]
    assert "admin_a@acme.com" in emails_a
    assert "staff_a@acme.com" in emails_a
    assert "admin_b@beta.com" not in emails_a

    # Admin B lists users: sees only Org B
    res_b = await client.get(f"{API}/auth/users", headers=two_orgs["admin_b_header"])
    assert res_b.status_code == 200
    emails_b = [u["email"] for u in res_b.json()["items"]]
    assert "admin_b@beta.com" in emails_b
    assert "admin_a@acme.com" not in emails_b

    # Admin A cannot modify Admin B's account
    patch_b = await client.patch(
        f"{API}/auth/users/{two_orgs['admin_b'].id}",
        headers=two_orgs["admin_a_header"],
        json={"is_active": False},
    )
    assert patch_b.status_code == 403


async def test_employee_cannot_edit_role_or_job_title(client: AsyncClient, two_orgs):
    # Staff A attempts to promote themselves to ADMIN and set job_title
    res = await client.patch(
        f"{API}/auth/me",
        headers=two_orgs["staff_a_header"],
        json={"full_name": "Updated Staff A", "job_title": "Chief Executive Officer", "phone": "+1 555 123 4567"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["full_name"] == "Updated Staff A"
    assert data["phone"] == "+1 555 123 4567"
    # job_title is ignored / unchanged for non-admin
    assert data["job_title"] is None
    assert data["role"] == "WAREHOUSE_STAFF"


async def test_cross_org_products_and_warehouses_isolation(client: AsyncClient, two_orgs):
    # Admin A creates a product in Org A
    prod_a_res = await client.post(
        f"{API}/products",
        headers=two_orgs["admin_a_header"],
        json={"sku": "PROD-A1", "name": "Org A Widget", "category": "Widgets", "unit_price": "10.00", "reorder_level": 5},
    )
    assert prod_a_res.status_code == 201
    prod_a_id = prod_a_res.json()["id"]

    # Admin A creates a warehouse in Org A
    wh_a_res = await client.post(
        f"{API}/warehouses",
        headers=two_orgs["admin_a_header"],
        json={"name": "Org A Main", "location": "Austin"},
    )
    assert wh_a_res.status_code == 201
    wh_a_id = wh_a_res.json()["id"]

    # Admin B cannot view, edit, or delete Org A's product
    assert (await client.get(f"{API}/products/{prod_a_id}", headers=two_orgs["admin_b_header"])).status_code == 404
    assert (await client.patch(f"{API}/products/{prod_a_id}", headers=two_orgs["admin_b_header"], json={"name": "Hacked"})).status_code == 404
    assert (await client.delete(f"{API}/products/{prod_a_id}", headers=two_orgs["admin_b_header"])).status_code == 404

    # Admin B cannot view, edit, or delete Org A's warehouse
    assert (await client.get(f"{API}/warehouses/{wh_a_id}", headers=two_orgs["admin_b_header"])).status_code == 404
    assert (await client.patch(f"{API}/warehouses/{wh_a_id}", headers=two_orgs["admin_b_header"], json={"name": "Hacked"})).status_code == 404
    assert (await client.delete(f"{API}/warehouses/{wh_a_id}", headers=two_orgs["admin_b_header"])).status_code == 404


async def test_cross_org_inventory_and_summary_isolation(client: AsyncClient, two_orgs):
    # Setup product and warehouse in Org A
    prod_a = (await client.post(
        f"{API}/products",
        headers=two_orgs["admin_a_header"],
        json={"sku": "ISO-A1", "name": "Item A", "category": "General", "unit_price": "50.00", "reorder_level": 10},
    )).json()
    wh_a = (await client.post(
        f"{API}/warehouses",
        headers=two_orgs["admin_a_header"],
        json={"name": "Warehouse A1", "location": "Chicago"},
    )).json()

    # Stock item in Org A
    inv_a = (await client.post(
        f"{API}/inventory",
        headers=two_orgs["admin_a_header"],
        json={"product_id": prod_a["id"], "warehouse_id": wh_a["id"], "quantity_on_hand": 100, "reserved_quantity": 10},
    )).json()

    # Admin B cannot view, update, or delete Org A's inventory item
    assert (await client.get(f"{API}/inventory/{inv_a['id']}", headers=two_orgs["admin_b_header"])).status_code == 404
    assert (await client.patch(f"{API}/inventory/{inv_a['id']}", headers=two_orgs["admin_b_header"], json={"quantity_on_hand": 0})).status_code == 404
    assert (await client.delete(f"{API}/inventory/{inv_a['id']}", headers=two_orgs["admin_b_header"])).status_code == 404

    # Check Org A's summary reflects its stock
    sum_a = (await client.get(f"{API}/inventory/summary", headers=two_orgs["admin_a_header"])).json()
    assert sum_a["total_quantity_on_hand"] >= 100
    assert float(sum_a["total_value"]) >= 5000.00

    # Org B's summary must NOT contain Org A's stock (should have 0 products/stock if empty)
    sum_b = (await client.get(f"{API}/inventory/summary", headers=two_orgs["admin_b_header"])).json()
    assert sum_b["total_products"] == 0
    assert sum_b["total_quantity_on_hand"] == 0
    assert float(sum_b["total_value"]) == 0.00

