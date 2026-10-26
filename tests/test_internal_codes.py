import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import User

API = "/api/v1"
INTERNAL = {"X-Internal-Key": settings.INTERNAL_API_KEY}


async def _lookup(client, email):
    r = await client.post(f"{API}/internal/login/lookup", headers=INTERNAL, json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()


async def _user(session_factory, email):
    async with session_factory() as s:
        return await s.scalar(select(User).where(User.email == email))


async def test_lookup_says_ok_for_an_active_verified_account(client, headers, session_factory):
    body = await _lookup(client, "Warehouse_Staff@x.com")
    user = await _user(session_factory, "warehouse_staff@x.com")
    assert body == {"status": "ok", "id": str(user.id), "full_name": None}


async def test_lookup_does_not_invent_unknown_accounts(client):
    assert await _lookup(client, "ghost@x.com") == {"status": "unknown", "id": None, "full_name": None}


async def test_lookup_reports_each_stage_a_person_can_be_stuck_at(client, headers, session_factory):
    await client.post(f"{API}/auth/register", json={"email": "new@x.com", "password": "longenough1"})
    assert (await _lookup(client, "new@x.com"))["status"] == "email_unverified"

    await client.post(f"{API}/internal/signup/verified", headers=INTERNAL, json={"email": "new@x.com"})
    assert (await _lookup(client, "new@x.com"))["status"] == "pending_approval"

    request_id = (await client.get(f"{API}/access-requests", headers=headers["ADMIN"])).json()["items"][0]["id"]
    await client.post(f"{API}/access-requests/{request_id}/reject", headers=headers["ADMIN"], json={})
    assert (await _lookup(client, "new@x.com"))["status"] == "rejected"

    async with session_factory() as s:
        staff = await s.scalar(select(User).where(User.email == "warehouse_staff@x.com"))
        staff.is_active = False
        await s.commit()
    assert (await _lookup(client, "warehouse_staff@x.com"))["status"] == "deactivated"


@pytest.mark.parametrize("path", ["login/lookup"])
async def test_lookup_needs_the_internal_key(client, path):
    for h in ({}, {"X-Internal-Key": "wrong"}):
        assert (await client.post(f"{API}/internal/{path}", headers=h, json={"email": "a@x.com"})).status_code == 403


async def test_setting_a_password_lets_the_person_sign_in_with_it(client, headers, session_factory):
    user = await _user(session_factory, "warehouse_staff@x.com")
    r = await client.post(f"{API}/internal/users/{user.id}/password", headers=INTERNAL, json={"password": "fresh-and-long-1"})
    assert r.status_code == 204
    assert (await client.post(f"{API}/auth/login", json={"email": "warehouse_staff@x.com", "password": "fresh-and-long-1"})).status_code == 200
    assert (await client.post(f"{API}/auth/login", json={"email": "warehouse_staff@x.com", "password": "pw"})).status_code == 401


@pytest.mark.parametrize("password", ["short", "é" * 37])
async def test_setting_an_unusable_password_is_refused(client, headers, session_factory, password):
    user = await _user(session_factory, "warehouse_staff@x.com")
    r = await client.post(f"{API}/internal/users/{user.id}/password", headers=INTERNAL, json={"password": password})
    assert r.status_code == 422


async def test_setting_a_password_needs_the_key_and_an_active_verified_account(client, headers, session_factory):
    user = await _user(session_factory, "warehouse_staff@x.com")
    assert (await client.post(f"{API}/internal/users/{user.id}/password", json={"password": "fresh-and-long-1"})).status_code == 403
    missing = await client.post(f"{API}/internal/users/00000000-0000-0000-0000-000000000000/password", headers=INTERNAL, json={"password": "fresh-and-long-1"})
    assert missing.status_code == 404
    async with session_factory() as s:
        (await s.get(User, user.id)).is_active = False
        await s.commit()
    assert (await client.post(f"{API}/internal/users/{user.id}/password", headers=INTERNAL, json={"password": "fresh-and-long-1"})).status_code == 404


async def test_health_is_ok_with_postgres_and_degraded_without_redis(client, monkeypatch):
    from redis.exceptions import RedisError

    from app.api.v1.endpoints import health

    class Down:
        async def ping(self):
            raise RedisError("down")

    r = await client.get(f"{API}/health")
    assert r.status_code == 200 and r.json()["postgres"] == "ok"

    monkeypatch.setattr(health, "redis_client", Down())
    r = await client.get(f"{API}/health")
    assert r.status_code == 200  # still serving: Redis only caches and throttles
    assert r.json() == {"status": "degraded", "postgres": "ok", "redis": "down"}


def test_api_docs_exist_in_development_but_not_in_production():
    from app.main import docs_urls

    assert docs_urls("development", "/api/v1") == {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/api/v1/openapi.json"}
    assert docs_urls("production", "/api/v1") == {"docs_url": None, "redoc_url": None, "openapi_url": None}


def test_the_product_is_called_invento():
    from app.core.config import Settings

    assert Settings(_env_file=None).APP_NAME == "Invento"
