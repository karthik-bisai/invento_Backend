import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import User

API = "/api/v1"
LOGIN = f"{API}/auth/login"
INTERNAL = {"X-Internal-Key": settings.INTERNAL_API_KEY}
PW = "longenough1"


async def _signup(client, email="new@x.com", name=None):
    body = {"email": email, "password": PW, **({"full_name": name} if name else {})}
    r = await client.post(f"{API}/auth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _verify(client, email="new@x.com"):
    r = await client.post(f"{API}/internal/signup/verified", headers=INTERNAL, json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()


async def _login(client, email="new@x.com", password=PW):
    return await client.post(LOGIN, json={"email": email, "password": password})


async def _token(client, email="new@x.com"):
    r = await _login(client, email)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---- profile, preferences, avatar, password ----------------------------------------------------


async def test_me_exposes_profile_fields_with_default_preferences(client, headers):
    body = (await client.get(f"{API}/auth/me", headers=headers["WAREHOUSE_STAFF"])).json()
    assert body["full_name"] is None and body["avatar_url"] is None and body["email_verified"] is True
    assert body["preferences"] == {
        "theme": "system",
        "compact_tables": False,
        "reduce_motion": False,
        "notify_low_stock": True,
        "notify_email_access_requests": True,
    }


async def test_profile_update_trims_and_blank_clears(client, headers):
    h = headers["WAREHOUSE_STAFF"]
    r = await client.patch(f"{API}/auth/me", headers=h, json={"full_name": "  Pat Lee ", "job_title": "Picker", "phone": "+1 (555) 010-2000"})
    assert r.status_code == 200 and r.json()["full_name"] == "Pat Lee" and r.json()["phone"] == "+1 (555) 010-2000"
    r = await client.patch(f"{API}/auth/me", headers=h, json={"job_title": "   "})
    assert r.json()["job_title"] is None and r.json()["full_name"] == "Pat Lee"  # untouched fields stay


@pytest.mark.parametrize("body", [{"phone": "call me"}, {"full_name": "x" * 121}])
async def test_profile_update_validates(client, headers, body):
    assert (await client.patch(f"{API}/auth/me", headers=headers["ADMIN"], json=body)).status_code == 422


async def test_profile_cannot_change_role_or_email(client, headers):
    h = headers["WAREHOUSE_STAFF"]
    await client.patch(f"{API}/auth/me", headers=h, json={"role": "ADMIN", "email": "evil@x.com", "is_active": False})
    me = (await client.get(f"{API}/auth/me", headers=h)).json()
    assert me["role"] == "WAREHOUSE_STAFF" and me["email"] == "warehouse_staff@x.com" and me["is_active"] is True


async def test_preferences_merge_and_validate(client, headers):
    h = headers["INVENTORY_MANAGER"]
    r = await client.patch(f"{API}/auth/me/preferences", headers=h, json={"theme": "dark", "compact_tables": True})
    assert r.json()["preferences"]["theme"] == "dark" and r.json()["preferences"]["compact_tables"] is True
    r = await client.patch(f"{API}/auth/me/preferences", headers=h, json={"notify_low_stock": False})
    prefs = r.json()["preferences"]
    assert prefs["notify_low_stock"] is False and prefs["theme"] == "dark"  # earlier choices survive a partial update
    assert (await client.patch(f"{API}/auth/me/preferences", headers=h, json={"theme": "neon"})).status_code == 422


async def test_avatar_must_point_at_our_media_host(client, headers):
    h = headers["WAREHOUSE_STAFF"]
    good = f"{settings.MEDIA_PUBLIC_URL}avatars/abc.png"
    assert (await client.put(f"{API}/auth/me/avatar", headers=h, json={"avatar_url": good})).json()["avatar_url"] == good
    for bad in ("https://evil.example/x.png", "javascript:alert(1)", "http://localhost:4000.evil.example/x.png"):
        assert (await client.put(f"{API}/auth/me/avatar", headers=h, json={"avatar_url": bad})).status_code == 422
    assert (await client.put(f"{API}/auth/me/avatar", headers=h, json={"avatar_url": None})).json()["avatar_url"] is None


async def test_password_change_requires_the_current_password(client, headers):
    h = headers["WAREHOUSE_STAFF"]
    wrong = await client.post(f"{API}/auth/me/password", headers=h, json={"current_password": "nope", "new_password": "brand-new-pass"})
    assert wrong.status_code == 400 and "current password" in wrong.json()["detail"]
    ok = await client.post(f"{API}/auth/me/password", headers=h, json={"current_password": "pw", "new_password": "brand-new-pass"})
    assert ok.status_code == 204
    assert (await _login(client, "warehouse_staff@x.com", "brand-new-pass")).status_code == 200
    assert (await _login(client, "warehouse_staff@x.com", "pw")).status_code == 401


@pytest.mark.parametrize("new", ["pw", "short", "é" * 37])
async def test_password_change_rejects_unusable_passwords(client, headers, new):
    r = await client.post(f"{API}/auth/me/password", headers=headers["ADMIN"], json={"current_password": "pw", "new_password": new})
    assert r.status_code == 422


async def test_password_change_is_throttled(client, headers):
    h = headers["ADMIN"]
    for _ in range(5):
        await client.post(f"{API}/auth/me/password", headers=h, json={"current_password": "wrong", "new_password": "brand-new-pass"})
    r = await client.post(f"{API}/auth/me/password", headers=h, json={"current_password": "pw", "new_password": "brand-new-pass"})
    assert r.status_code == 429


# ---- gateway-only endpoints ---------------------------------------------------------------------


@pytest.mark.parametrize("key", [None, "wrong", ""])
async def test_internal_endpoints_need_the_shared_key(client, key):
    h = {"X-Internal-Key": key} if key is not None else {}
    for path in ("signup/status", "signup/verified"):
        assert (await client.post(f"{API}/internal/{path}", headers=h, json={"email": "a@x.com"})).status_code == 403


async def test_signup_status_reports_existence_and_verification(client):
    assert (await client.post(f"{API}/internal/signup/status", headers=INTERNAL, json={"email": "ghost@x.com"})).json() == {
        "exists": False,
        "email_verified": False,
        "full_name": None,
    }
    await _signup(client, name="Pat Lee")
    body = (await client.post(f"{API}/internal/signup/status", headers=INTERNAL, json={"email": "NEW@x.com"})).json()
    assert body == {"exists": True, "email_verified": False, "full_name": "Pat Lee"}


# ---- sign-up -> verify -> approval ---------------------------------------------------------------


async def test_login_explains_each_stage_of_the_journey(client, headers):
    def status_of(r):
        assert r.status_code == 403
        return r.headers["x-account-status"]

    await _signup(client)
    assert status_of(await _login(client)) == "email_unverified"

    await _verify(client)
    blocked = await _login(client)
    assert status_of(blocked) == "pending_approval" and "administrator" in blocked.json()["detail"]

    request_id = (await client.get(f"{API}/access-requests", headers=headers["ADMIN"])).json()["items"][0]["id"]
    await client.post(f"{API}/access-requests/{request_id}/reject", headers=headers["ADMIN"], json={"note": "Not a customer"})
    assert status_of(await _login(client)) == "rejected"


async def test_wrong_password_never_reveals_the_account_stage(client):
    await _signup(client)
    r = await _login(client, password="wrong-password")
    assert r.status_code == 401 and "x-account-status" not in r.headers


async def test_verifying_opens_one_request_and_tells_admins(client, headers):
    await _signup(client, name="Pat Lee")
    result = await _verify(client)
    assert result["auto_approved"] is False and result["request_id"] and result["user"]["email"] == "new@x.com"
    assert [a["email"] for a in result["admins"]] == ["admin@x.com"] and result["admins"][0]["wants_email"] is True

    inbox = (await client.get(f"{API}/notifications", headers=headers["ADMIN"])).json()
    assert inbox["unread"] == 1 and inbox["items"][0]["kind"] == "access_requested"
    assert "Pat Lee" in inbox["items"][0]["body"] and inbox["items"][0]["link"] == "/users?tab=requests"

    again = await _verify(client)  # replaying the verification must not spam admins
    assert again["admins"] == [] and again["request_id"] == result["request_id"]
    assert (await client.get(f"{API}/notifications", headers=headers["ADMIN"])).json()["unread"] == 1
    assert (await client.get(f"{API}/access-requests", headers=headers["ADMIN"])).json()["total"] == 1


async def test_admins_who_opted_out_of_email_are_flagged(client, headers):
    await client.patch(f"{API}/auth/me/preferences", headers=headers["ADMIN"], json={"notify_email_access_requests": False})
    await _signup(client)
    assert (await _verify(client))["admins"][0]["wants_email"] is False


async def test_verifying_an_unknown_email_is_404(client):
    r = await client.post(f"{API}/internal/signup/verified", headers=INTERNAL, json={"email": "ghost@x.com"})
    assert r.status_code == 404


async def test_auto_activate_skips_the_approval_step(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_AUTO_ACTIVATE", True)
    await _signup(client)
    result = await _verify(client)
    assert result["auto_approved"] is True and result["admins"] == [] and result["request_id"] is None
    assert (await _login(client)).status_code == 200


async def test_admin_approves_with_a_role_and_the_user_can_sign_in(client, headers):
    await _signup(client)
    request_id = (await _verify(client))["request_id"]

    r = await client.post(f"{API}/access-requests/{request_id}/approve", headers=headers["ADMIN"], json={"role": "INVENTORY_MANAGER"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["user"]["email"] == "new@x.com"

    auth = await _token(client)
    me = (await client.get(f"{API}/auth/me", headers=auth)).json()
    assert me["role"] == "INVENTORY_MANAGER" and me["is_active"] is True
    inbox = (await client.get(f"{API}/notifications", headers=auth)).json()
    assert [n["kind"] for n in inbox["items"]] == ["access_approved"]


async def test_approve_defaults_to_warehouse_staff_without_a_body(client, headers):
    await _signup(client)
    request_id = (await _verify(client))["request_id"]
    assert (await client.post(f"{API}/access-requests/{request_id}/approve", headers=headers["ADMIN"])).status_code == 200
    assert (await client.get(f"{API}/auth/me", headers=await _token(client))).json()["role"] == "WAREHOUSE_STAFF"


async def test_reject_records_the_reason_and_keeps_the_account_locked(client, headers, session_factory):
    await _signup(client)
    request_id = (await _verify(client))["request_id"]
    r = await client.post(f"{API}/access-requests/{request_id}/reject", headers=headers["ADMIN"], json={"note": "  Not on the team  "})
    assert r.json()["status"] == "REJECTED" and r.json()["note"] == "Not on the team"
    async with session_factory() as s:
        assert (await s.scalar(select(User).where(User.email == "new@x.com"))).is_active is False


async def test_a_request_can_only_be_decided_once(client, headers):
    await _signup(client)
    request_id = (await _verify(client))["request_id"]
    await client.post(f"{API}/access-requests/{request_id}/approve", headers=headers["ADMIN"])
    for action in ("approve", "reject"):
        r = await client.post(f"{API}/access-requests/{request_id}/{action}", headers=headers["ADMIN"])
        assert r.status_code == 409 and "already approved" in r.json()["detail"]


async def test_deciding_an_unknown_request_is_404(client, headers):
    r = await client.post(f"{API}/access-requests/00000000-0000-0000-0000-000000000000/approve", headers=headers["ADMIN"])
    assert r.status_code == 404


async def test_requests_can_be_filtered_by_status(client, headers):
    for email in ("a@x.com", "b@x.com"):
        await _signup(client, email)
    first = (await _verify(client, "a@x.com"))["request_id"]
    await _verify(client, "b@x.com")
    await client.post(f"{API}/access-requests/{first}/approve", headers=headers["ADMIN"])

    def emails(r):
        return [i["user"]["email"] for i in r.json()["items"]]

    assert emails(await client.get(f"{API}/access-requests", headers=headers["ADMIN"], params={"status": "PENDING"})) == ["b@x.com"]
    assert emails(await client.get(f"{API}/access-requests", headers=headers["ADMIN"], params={"status": "APPROVED"})) == ["a@x.com"]
    assert (await client.get(f"{API}/access-requests", headers=headers["ADMIN"])).json()["total"] == 2


@pytest.mark.parametrize("role", ["INVENTORY_MANAGER", "WAREHOUSE_STAFF"])
async def test_only_admins_handle_access_requests(client, headers, role):
    assert (await client.get(f"{API}/access-requests", headers=headers[role])).status_code == 403
    uid = "00000000-0000-0000-0000-000000000000"
    assert (await client.post(f"{API}/access-requests/{uid}/approve", headers=headers[role])).status_code == 403


async def test_activating_from_the_users_page_settles_the_waiting_request(client, headers):
    await _signup(client)
    await _verify(client)
    uid = (await client.get(f"{API}/auth/users", headers=headers["ADMIN"], params={"q": "new@"})).json()["items"][0]["id"]
    assert (await client.patch(f"{API}/auth/users/{uid}", headers=headers["ADMIN"], json={"is_active": True})).status_code == 200
    items = (await client.get(f"{API}/access-requests", headers=headers["ADMIN"])).json()["items"]
    assert [i["status"] for i in items] == ["APPROVED"]
    assert (await _login(client)).status_code == 200


async def test_a_deactivated_account_gets_a_distinct_message(client, headers, session_factory):
    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.email == "warehouse_staff@x.com"))
        user.is_active = False
        await s.commit()
    r = await _login(client, "warehouse_staff@x.com", "pw")
    assert r.status_code == 403 and r.headers["x-account-status"] == "deactivated"


# ---- notifications ------------------------------------------------------------------------------


async def test_notifications_can_be_marked_read_one_by_one_or_all(client, headers):
    for email in ("a@x.com", "b@x.com"):
        await _signup(client, email)
        await _verify(client, email)
    admin = headers["ADMIN"]
    inbox = (await client.get(f"{API}/notifications", headers=admin)).json()
    assert inbox["unread"] == 2 and len(inbox["items"]) == 2

    first = inbox["items"][0]["id"]
    assert (await client.post(f"{API}/notifications/{first}/read", headers=admin)).status_code == 204
    after = (await client.get(f"{API}/notifications", headers=admin)).json()
    assert after["unread"] == 1 and sum(1 for n in after["items"] if n["read_at"]) == 1

    assert (await client.post(f"{API}/notifications/read-all", headers=admin)).status_code == 204
    assert (await client.get(f"{API}/notifications", headers=admin)).json()["unread"] == 0


async def test_notifications_are_private_to_their_owner(client, headers):
    await _signup(client)
    await _verify(client)
    note_id = (await client.get(f"{API}/notifications", headers=headers["ADMIN"])).json()["items"][0]["id"]
    assert (await client.post(f"{API}/notifications/{note_id}/read", headers=headers["INVENTORY_MANAGER"])).status_code == 404
    assert (await client.get(f"{API}/notifications", headers=headers["INVENTORY_MANAGER"])).json()["items"] == []


async def test_notifications_require_sign_in(client):
    assert (await client.get(f"{API}/notifications")).status_code == 401


# ---- service tokens for the gateway --------------------------------------------------------------


async def test_service_token_acts_as_that_user_and_expires_soon(client, headers, session_factory):
    from app.core.security import decode_access_token

    async with session_factory() as s:
        admin = await s.scalar(select(User).where(User.email == "admin@x.com"))
    r = await client.post(f"{API}/internal/users/{admin.id}/token", headers=INTERNAL)
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "admin@x.com"
    payload = decode_access_token(token)
    from datetime import datetime, timezone

    assert 0 < payload["exp"] - datetime.now(timezone.utc).timestamp() <= 120


async def test_service_token_needs_the_key_and_an_active_verified_user(client, headers, session_factory):
    async with session_factory() as s:
        staff = await s.scalar(select(User).where(User.email == "warehouse_staff@x.com"))
        staff_id = staff.id
    assert (await client.post(f"{API}/internal/users/{staff_id}/token")).status_code == 403
    assert (await client.post(f"{API}/internal/users/{staff_id}/token", headers={"X-Internal-Key": "nope"})).status_code == 403
    missing = await client.post(f"{API}/internal/users/00000000-0000-0000-0000-000000000000/token", headers=INTERNAL)
    assert missing.status_code == 404
    async with session_factory() as s:
        (await s.get(User, staff_id)).is_active = False
        await s.commit()
    assert (await client.post(f"{API}/internal/users/{staff_id}/token", headers=INTERNAL)).status_code == 404
