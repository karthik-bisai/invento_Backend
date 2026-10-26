"""Role-based access control: every protected action is checked for every role (and anonymous callers)."""
import pytest

API = "/api/v1"
ROLES = ["ADMIN", "INVENTORY_MANAGER", "WAREHOUSE_STAFF"]
ADMIN = {"ADMIN"}
MANAGERS = {"ADMIN", "INVENTORY_MANAGER"}
EVERYONE = set(ROLES)


# action name -> (roles allowed, request factory(ids) -> (method, url, json))
ACTIONS = {
    "create_product": (MANAGERS, lambda i: ("POST", f"{API}/products", {"sku": "NEW", "name": "n", "category": "c", "unit_price": "1"})),
    "set_reorder_level": (MANAGERS, lambda i: ("PATCH", f"{API}/products/{i['product']['id']}/reorder-level", {"reorder_level": 7})),
    "update_product": (ADMIN, lambda i: ("PATCH", f"{API}/products/{i['product']['id']}", {"name": "renamed"})),
    "delete_product": (ADMIN, lambda i: ("DELETE", f"{API}/products/{i['product']['id']}", None)),
    "create_warehouse": (MANAGERS, lambda i: ("POST", f"{API}/warehouses", {"name": "Second", "location": "LA"})),
    "update_warehouse": (ADMIN, lambda i: ("PATCH", f"{API}/warehouses/{i['warehouse']['id']}", {"location": "Boston"})),
    "delete_warehouse": (ADMIN, lambda i: ("DELETE", f"{API}/warehouses/{i['warehouse']['id']}", None)),
    "create_stock_record": (MANAGERS, lambda i: ("POST", f"{API}/inventory", {"product_id": i["product"]["id"], "warehouse_id": i["warehouse"]["id"]})),
    "update_stock_counts": (EVERYONE, lambda i: ("PATCH", f"{API}/inventory/{i['item']['id']}", {"quantity_on_hand": 50})),
    "delete_stock_record": (ADMIN, lambda i: ("DELETE", f"{API}/inventory/{i['item']['id']}", None)),
    "create_user": (ADMIN, lambda i: ("POST", f"{API}/auth/users", {"email": "n@x.com", "password": "longenough1"})),
}

READS = [
    "/products",
    "/warehouses",
    "/inventory",
    "/inventory/summary",
    "/inventory/low-stock",
    "/auth/me",
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("action", ACTIONS)
async def test_permission_matrix(client, headers, seed, action, role):
    allowed, build = ACTIONS[action]
    ids = await seed()
    method, url, body = build(ids)

    r = await client.request(method, url, headers=headers[role], json=body)

    if role in allowed:
        # Permitted roles may still get a business-rule status (e.g. 409 deleting a warehouse that holds stock),
        # but never an auth failure.
        assert r.status_code not in (401, 403), f"{role} should be allowed to {action}: {r.status_code} {r.text}"
    else:
        assert r.status_code == 403, f"{role} must NOT be able to {action}: {r.status_code} {r.text}"


@pytest.mark.parametrize("action", ACTIONS)
async def test_anonymous_callers_are_rejected_everywhere(client, seed, action):
    _, build = ACTIONS[action]
    ids = await seed()
    method, url, body = build(ids)
    assert (await client.request(method, url, json=body)).status_code == 401


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("path", READS)
async def test_every_role_can_read(client, headers, path, role):
    assert (await client.get(f"{API}{path}", headers=headers[role])).status_code == 200


@pytest.mark.parametrize("path", [p for p in READS if p != "/auth/me"])
async def test_reads_require_authentication(client, path):
    assert (await client.get(f"{API}{path}")).status_code == 401


# ---- the headline cases, spelled out ---------------------------------------------------------


async def test_warehouse_staff_cannot_delete_a_product(client, headers, seed):
    ids = await seed()
    r = await client.delete(f"{API}/products/{ids['product']['id']}", headers=headers["WAREHOUSE_STAFF"])
    assert r.status_code == 403
    assert r.json()["detail"] == "Insufficient permissions"
    # ...and nothing was actually deleted.
    assert (await client.get(f"{API}/products/{ids['product']['id']}", headers=headers["ADMIN"])).status_code == 200


async def test_forbidden_write_does_not_change_data_or_invalidate_cache(client, headers, seed, redis):
    ids = await seed()
    await client.get(f"{API}/inventory/summary", headers=headers["ADMIN"])  # warm the cache
    cached_before = dict(redis.store)

    r = await client.patch(
        f"{API}/products/{ids['product']['id']}/reorder-level", headers=headers["WAREHOUSE_STAFF"], json={"reorder_level": 1}
    )

    assert r.status_code == 403
    assert redis.store == cached_before
    product = (await client.get(f"{API}/products/{ids['product']['id']}", headers=headers["ADMIN"])).json()
    assert product["reorder_level"] == 20


async def test_inventory_manager_cannot_delete_or_edit_beyond_reorder_level(client, headers, seed):
    ids = await seed()
    mgr = headers["INVENTORY_MANAGER"]
    assert (await client.delete(f"{API}/products/{ids['product']['id']}", headers=mgr)).status_code == 403
    assert (await client.patch(f"{API}/products/{ids['product']['id']}", headers=mgr, json={"unit_price": "99"})).status_code == 403
    assert (await client.patch(f"{API}/products/{ids['product']['id']}/reorder-level", headers=mgr, json={"reorder_level": 5})).status_code == 200
