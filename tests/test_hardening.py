import pytest
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.api.deps import get_redis
from app.api.rate_limit import reset_local_state
from app.core.config import Settings, settings
from app.main import app

API = "/api/v1"
LOGIN = f"{API}/auth/login"
REGISTER = f"{API}/auth/register"


@pytest.fixture(autouse=True)
def _clean_local_counters():
    reset_local_state()
    yield
    reset_local_state()


def _login(client, email="admin@x.com", password="wrong-password", **kw):
    return client.post(LOGIN, json={"email": email, "password": password}, **kw)


# ---- login throttling ---------------------------------------------------------------------


async def test_login_is_throttled_per_email_with_retry_after(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE", 3)
    for _ in range(3):
        assert (await _login(client)).status_code == 401
    blocked = await _login(client)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"].isdigit() and int(blocked.headers["retry-after"]) > 0
    assert "Try again in" in blocked.json()["detail"]


async def test_throttle_is_per_email_so_one_target_cannot_lock_out_others(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE", 2)
    for _ in range(3):
        await _login(client, "admin@x.com")
    assert (await _login(client, "admin@x.com")).status_code == 429
    ok = await _login(client, "inventory_manager@x.com", "pw")
    assert ok.status_code == 200


async def test_throttle_applies_even_to_the_correct_password(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE", 2)
    for _ in range(2):
        await _login(client)
    assert (await _login(client, password="pw")).status_code == 429  # a guesser can't win by luck on attempt N+1


async def test_login_is_also_throttled_per_address_across_emails(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE_PER_IP", 3)
    for i in range(3):
        assert (await _login(client, f"user{i}@x.com")).status_code == 401
    assert (await _login(client, "user99@x.com")).status_code == 429


async def test_rate_limiting_can_be_switched_off(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE", 1)
    for _ in range(4):
        assert (await _login(client)).status_code == 401


# ---- sign-up throttling -------------------------------------------------------------------


async def test_registration_is_throttled_per_address(client, monkeypatch):
    monkeypatch.setattr(settings, "REGISTRATIONS_PER_HOUR", 2)
    for i in range(2):
        assert (await client.post(REGISTER, json={"email": f"n{i}@x.com", "password": "longenough1"})).status_code == 201
    blocked = await client.post(REGISTER, json={"email": "n9@x.com", "password": "longenough1"})
    assert blocked.status_code == 429 and "retry-after" in blocked.headers


# ---- client address handling --------------------------------------------------------------


async def test_forwarded_for_header_cannot_dodge_the_limit_by_default(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE", 2)
    for i in range(2):
        await _login(client, headers={"X-Forwarded-For": f"10.0.0.{i}"})
    assert (await _login(client, headers={"X-Forwarded-For": "10.0.0.99"})).status_code == 429


async def test_forwarded_for_is_used_when_behind_a_trusted_proxy(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE", 1)
    await _login(client, headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"})
    assert (await _login(client, headers={"X-Forwarded-For": "203.0.113.5"})).status_code == 429  # same real client
    assert (await _login(client, headers={"X-Forwarded-For": "203.0.113.6"})).status_code == 401  # a different one


# ---- Redis outage -------------------------------------------------------------------------


class BrokenRedis:
    async def incr(self, *_):
        raise RedisError("down")

    expire = ttl = get = set = delete = incr


async def test_limits_still_hold_when_redis_is_down(client, headers, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_ATTEMPTS_PER_MINUTE", 2)
    app.dependency_overrides[get_redis] = lambda: BrokenRedis()
    for _ in range(2):
        assert (await _login(client)).status_code == 401
    assert (await _login(client)).status_code == 429  # fell back to the in-process counter, did not fail open


class ExpirylessRedis:
    """A counter that lost its TTL (crash between INCR and EXPIRE) must not lock the address out for good."""

    def __init__(self):
        self.expired_with: int | None = None

    async def incr(self, _):
        return 5

    async def ttl(self, _):
        return -1

    async def expire(self, _, seconds):
        self.expired_with = seconds


async def test_a_counter_without_expiry_is_given_one(client, headers, monkeypatch):
    fake = ExpirylessRedis()
    app.dependency_overrides[get_redis] = lambda: fake
    await _login(client)
    assert fake.expired_with == 60


# ---- passwords ----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["register", "users"])
async def test_password_over_72_bytes_is_rejected_not_crashed(client, headers, path):
    too_long = "é" * 37  # 37 characters but 74 bytes: bcrypt would otherwise reject or truncate it
    r = await client.post(f"{API}/auth/{path}", headers=headers["ADMIN"], json={"email": "z@x.com", "password": too_long})
    assert r.status_code == 422
    assert "72 bytes" in r.text


async def test_password_of_exactly_72_bytes_is_accepted(client):
    r = await client.post(REGISTER, json={"email": "edge@x.com", "password": "a" * 72})
    assert r.status_code == 201


# ---- login timing / enumeration -----------------------------------------------------------


async def test_unknown_email_and_wrong_password_look_identical(client, headers):
    unknown = await _login(client, "ghost@x.com")
    wrong = await _login(client, "admin@x.com")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


# ---- production configuration -------------------------------------------------------------


def test_production_refuses_default_secrets():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, ENVIRONMENT="production")


def test_production_boots_with_real_secrets():
    s = Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY="x" * 40, FIRST_ADMIN_PASSWORD="a-real-password-1", INTERNAL_API_KEY="y" * 40)
    assert s.ENVIRONMENT == "production"


def test_development_keeps_working_with_defaults():
    assert Settings(_env_file=None).ENVIRONMENT == "development"
