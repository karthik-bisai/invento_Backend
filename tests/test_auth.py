import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.models import User

API = "/api/v1"
LOGIN = f"{API}/auth/login"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- security utilities -------------------------------------------------------------------


def test_password_hash_is_salted_and_verifiable():
    first, second = hash_password("s3cret-pass"), hash_password("s3cret-pass")
    assert first != "s3cret-pass" and first != second  # never plaintext, salted per hash
    assert verify_password("s3cret-pass", first)
    assert not verify_password("wrong", first)


def test_token_round_trip_and_rejection():
    token = create_access_token("user-1", "ADMIN")
    payload = decode_access_token(token)
    assert payload and payload["sub"] == "user-1" and payload["role"] == "ADMIN"
    assert decode_access_token(token + "x") is None  # tampered signature
    assert decode_access_token("not.a.jwt") is None
    assert decode_access_token(create_access_token("u", "ADMIN", timedelta(seconds=-1))) is None  # expired


# ---- login --------------------------------------------------------------------------------


async def test_login_returns_bearer_token_for_valid_credentials(client, headers):
    r = await client.post(LOGIN, json={"email": "admin@x.com", "password": "pw"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert decode_access_token(body["access_token"])["role"] == "ADMIN"


async def test_login_is_case_insensitive_on_email(client, headers):
    r = await client.post(LOGIN, json={"email": "ADMIN@X.com", "password": "pw"})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "creds",
    [
        {"email": "admin@x.com", "password": "wrong"},
        {"email": "nobody@x.com", "password": "pw"},
    ],
)
async def test_login_failure_does_not_reveal_which_part_was_wrong(client, headers, creds):
    r = await client.post(LOGIN, json=creds)
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password"


@pytest.mark.parametrize("payload", [{}, {"email": "admin@x.com"}, {"email": "not-an-email", "password": "pw"}])
async def test_login_rejects_malformed_payloads(client, payload):
    assert (await client.post(LOGIN, json=payload)).status_code == 422


async def test_inactive_user_cannot_log_in_or_use_existing_token(client, headers, session_factory):
    token = headers["WAREHOUSE_STAFF"]["Authorization"].removeprefix("Bearer ")
    assert (await client.get(f"{API}/auth/me", headers=bearer(token))).status_code == 200

    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.email == "warehouse_staff@x.com"))
        user.is_active = False
        await s.commit()

    assert (await client.post(LOGIN, json={"email": "warehouse_staff@x.com", "password": "pw"})).status_code == 403
    assert (await client.get(f"{API}/auth/me", headers=bearer(token))).status_code == 401


# ---- authenticated access ------------------------------------------------------------------


async def test_me_returns_profile_without_password_hash(client, headers):
    r = await client.get(f"{API}/auth/me", headers=headers["INVENTORY_MANAGER"])
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "inventory_manager@x.com" and body["role"] == "INVENTORY_MANAGER" and body["is_active"] is True
    assert "hashed_password" not in body and "password" not in body


async def test_me_without_credentials_is_401_with_bearer_challenge(client):
    r = await client.get(f"{API}/auth/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "authorization",
    ["Bearer garbage", "Bearer ", "Basic YWRtaW46cHc=", "garbage"],
)
async def test_me_rejects_bad_authorization_headers(client, authorization):
    assert (await client.get(f"{API}/auth/me", headers={"Authorization": authorization})).status_code == 401


async def test_expired_token_is_rejected(client, headers, session_factory):
    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.email == "admin@x.com"))
    expired = create_access_token(str(user.id), "ADMIN", timedelta(minutes=-1))
    assert (await client.get(f"{API}/auth/me", headers=bearer(expired))).status_code == 401


async def test_token_for_deleted_user_is_rejected(client, headers):
    ghost = create_access_token(str(uuid.uuid4()), "ADMIN")
    assert (await client.get(f"{API}/auth/me", headers=bearer(ghost))).status_code == 401


async def test_role_claim_in_token_is_not_trusted(client, headers, session_factory):
    """A staff user holding a token that *claims* ADMIN must still be treated as staff (role comes from the DB)."""
    async with session_factory() as s:
        staff = await s.scalar(select(User).where(User.email == "warehouse_staff@x.com"))
    forged = create_access_token(str(staff.id), "ADMIN")
    body = {"email": "new@x.com", "password": "longenough1", "role": "ADMIN"}
    assert (await client.post(f"{API}/auth/users", headers=bearer(forged), json=body)).status_code == 403


# ---- user creation (admin only) ------------------------------------------------------------


async def test_admin_creates_user_who_can_then_log_in(client, headers):
    r = await client.post(
        f"{API}/auth/users",
        headers=headers["ADMIN"],
        json={"email": "New.Hire@X.com", "password": "longenough1", "role": "WAREHOUSE_STAFF"},
    )
    assert r.status_code == 201
    assert r.json()["email"] == "new.hire@x.com" and "hashed_password" not in r.json()
    login = await client.post(LOGIN, json={"email": "new.hire@x.com", "password": "longenough1"})
    assert login.status_code == 200


async def test_create_user_validates_input_and_rejects_duplicates(client, headers):
    admin = headers["ADMIN"]
    assert (await client.post(f"{API}/auth/users", headers=admin, json={"email": "a@b.com", "password": "short"})).status_code == 422
    assert (await client.post(f"{API}/auth/users", headers=admin, json={"email": "a@b.com", "password": "longenough1", "role": "SUPERUSER"})).status_code == 422
    dup = {"email": "ADMIN@x.com", "password": "longenough1"}
    assert (await client.post(f"{API}/auth/users", headers=admin, json=dup)).status_code == 409  # case-insensitive


@pytest.mark.parametrize("role", ["INVENTORY_MANAGER", "WAREHOUSE_STAFF"])
async def test_non_admins_cannot_create_users(client, headers, role):
    r = await client.post(f"{API}/auth/users", headers=headers[role], json={"email": "x@y.com", "password": "longenough1"})
    assert r.status_code == 403


# ---- public sign-up -------------------------------------------------------------------------


async def test_register_creates_inactive_staff_account_awaiting_approval(client):
    r = await client.post(f"{API}/auth/register", json={"email": "New@X.com", "password": "longenough1", "role": "ADMIN"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@x.com" and body["role"] == "WAREHOUSE_STAFF" and body["is_active"] is False  # role input ignored
    login = await client.post(LOGIN, json={"email": "new@x.com", "password": "longenough1"})
    assert login.status_code == 403 and login.headers["x-account-status"] == "email_unverified"  # must confirm the email first


async def test_register_can_auto_activate(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SIGNUP_AUTO_ACTIVATE", True)
    assert (await client.post(f"{API}/auth/register", json={"email": "a@x.com", "password": "longenough1"})).json()["is_active"] is True
    blocked = await client.post(LOGIN, json={"email": "a@x.com", "password": "longenough1"})
    assert blocked.status_code == 403 and blocked.headers["x-account-status"] == "email_unverified"  # active, but still unverified


async def test_register_validates_and_rejects_duplicates(client, headers):
    assert (await client.post(f"{API}/auth/register", json={"email": "bad", "password": "longenough1"})).status_code == 422
    assert (await client.post(f"{API}/auth/register", json={"email": "a@x.com", "password": "short"})).status_code == 422
    assert (await client.post(f"{API}/auth/register", json={"email": "ADMIN@x.com", "password": "longenough1"})).status_code == 409


async def test_wrong_password_on_inactive_account_stays_401(client):
    await client.post(f"{API}/auth/register", json={"email": "p@x.com", "password": "longenough1"})
    assert (await client.post(LOGIN, json={"email": "p@x.com", "password": "wrongwrong"})).status_code == 401
