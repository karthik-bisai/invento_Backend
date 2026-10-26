import pytest
from sqlalchemy import select

from app.models import User

API = "/api/v1"


async def _user_id(session_factory, email: str) -> str:
    async with session_factory() as s:
        return str((await s.scalar(select(User).where(User.email == email))).id)


async def test_admin_lists_users_with_search_and_paging(client, headers):
    r = await client.get(f"{API}/auth/users", headers=headers["ADMIN"])
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 3
    assert all("hashed_password" not in u for u in body["items"])

    r = await client.get(f"{API}/auth/users", headers=headers["ADMIN"], params={"q": "manager", "size": 1})
    assert [u["email"] for u in r.json()["items"]] == ["inventory_manager@x.com"]


@pytest.mark.parametrize("role", ["INVENTORY_MANAGER", "WAREHOUSE_STAFF"])
async def test_non_admins_cannot_list_or_update_users(client, headers, session_factory, role):
    assert (await client.get(f"{API}/auth/users", headers=headers[role])).status_code == 403
    uid = await _user_id(session_factory, "warehouse_staff@x.com")
    assert (await client.patch(f"{API}/auth/users/{uid}", headers=headers[role], json={"is_active": False})).status_code == 403


async def test_admin_changes_role_and_deactivates_user(client, headers, session_factory):
    uid = await _user_id(session_factory, "warehouse_staff@x.com")
    r = await client.patch(f"{API}/auth/users/{uid}", headers=headers["ADMIN"], json={"role": "INVENTORY_MANAGER", "is_active": False})
    assert r.status_code == 200
    assert r.json()["role"] == "INVENTORY_MANAGER" and r.json()["is_active"] is False
    login = await client.post(f"{API}/auth/login", json={"email": "warehouse_staff@x.com", "password": "pw"})
    assert login.status_code == 403


async def test_admin_resets_password(client, headers, session_factory):
    uid = await _user_id(session_factory, "warehouse_staff@x.com")
    r = await client.patch(f"{API}/auth/users/{uid}", headers=headers["ADMIN"], json={"password": "brand-new-pass"})
    assert r.status_code == 200
    ok = await client.post(f"{API}/auth/login", json={"email": "warehouse_staff@x.com", "password": "brand-new-pass"})
    assert ok.status_code == 200


async def test_admin_cannot_lock_themselves_out(client, headers, session_factory):
    uid = await _user_id(session_factory, "admin@x.com")
    for body in ({"is_active": False}, {"role": "WAREHOUSE_STAFF"}):
        r = await client.patch(f"{API}/auth/users/{uid}", headers=headers["ADMIN"], json=body)
        assert r.status_code == 400


async def test_update_unknown_user_is_404(client, headers):
    r = await client.patch(f"{API}/auth/users/00000000-0000-0000-0000-000000000000", headers=headers["ADMIN"], json={"is_active": True})
    assert r.status_code == 404
