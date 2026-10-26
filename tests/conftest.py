import pytest
from sqlalchemy import event
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db, get_redis
from app.core.database import Base
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Role, User


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    async def delete(self, key):
        self.store.pop(key, None)

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def ttl(self, key):
        return self.ttls.get(key) if self.ttls.get(key) is not None else -1


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _):  # SQLite ignores FKs by default; Postgres enforces them
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
async def client(session_factory, redis):
    async def _db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


API = "/api/v1"
PASSWORD_HASH = hash_password("pw")  # bcrypt is deliberately slow; hash once, not per test


@pytest.fixture
def seed(client, headers):
    """Factory: create a product, warehouse and one stock record. Returns their JSON as a dict."""

    async def _seed(on_hand: int = 10, reserved: int = 2, reorder_level: int = 20, sku: str = "A1", warehouse: str = "Main"):
        admin = headers["ADMIN"]
        product = (
            await client.post(
                f"{API}/products",
                headers=admin,
                json={"sku": sku, "name": f"Product {sku}", "category": "parts", "unit_price": "2.50", "reorder_level": reorder_level},
            )
        ).json()
        wh = (await client.post(f"{API}/warehouses", headers=admin, json={"name": warehouse, "location": "NYC"})).json()
        item = (
            await client.post(
                f"{API}/inventory",
                headers=admin,
                json={"product_id": product["id"], "warehouse_id": wh["id"], "quantity_on_hand": on_hand, "reserved_quantity": reserved},
            )
        ).json()
        return {"product": product, "warehouse": wh, "item": item}

    return _seed


@pytest.fixture
async def headers(session_factory):
    """Auth headers for each role, keyed by role name."""
    out = {}
    async with session_factory() as s:
        for role in Role:
            u = User(email=f"{role.value.lower()}@x.com", hashed_password=PASSWORD_HASH, role=role, email_verified=True)
            s.add(u)
            await s.commit()
            out[role.value] = {"Authorization": f"Bearer {create_access_token(str(u.id), role.value)}"}
    return out
