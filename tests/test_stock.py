"""Stock-count logic: increments/decrements, invariants, and their effect on derived figures."""
import pytest

API = "/api/v1"


async def patch_stock(client, headers, item_id, **body):
    return await client.patch(f"{API}/inventory/{item_id}", headers=headers, json=body)


async def get_item(client, headers, item_id):
    return (await client.get(f"{API}/inventory/{item_id}", headers=headers)).json()


async def test_increment_increases_on_hand_and_available(client, headers, seed):
    ids = await seed(on_hand=10, reserved=2)
    r = await patch_stock(client, headers["WAREHOUSE_STAFF"], ids["item"]["id"], quantity_on_hand=35)
    assert r.status_code == 200
    body = r.json()
    assert (body["quantity_on_hand"], body["reserved_quantity"], body["available_quantity"]) == (35, 2, 33)


async def test_increment_is_persisted(client, headers, seed):
    ids = await seed(on_hand=10)
    await patch_stock(client, headers["WAREHOUSE_STAFF"], ids["item"]["id"], quantity_on_hand=11)
    assert (await get_item(client, headers["ADMIN"], ids["item"]["id"]))["quantity_on_hand"] == 11


async def test_repeated_increments_accumulate_correctly(client, headers, seed):
    ids = await seed(on_hand=0, reserved=0)
    item_id, staff = ids["item"]["id"], headers["WAREHOUSE_STAFF"]
    current = 0
    for delta in (5, 12, 1, 100):
        current += delta
        assert (await patch_stock(client, staff, item_id, quantity_on_hand=current)).json()["quantity_on_hand"] == current
    assert current == 118


async def test_partial_update_leaves_other_field_untouched(client, headers, seed):
    ids = await seed(on_hand=10, reserved=2)
    item_id, staff = ids["item"]["id"], headers["WAREHOUSE_STAFF"]
    assert (await patch_stock(client, staff, item_id, reserved_quantity=7)).json()["quantity_on_hand"] == 10
    assert (await patch_stock(client, staff, item_id, quantity_on_hand=20)).json()["reserved_quantity"] == 7


async def test_empty_patch_is_a_no_op(client, headers, seed):
    ids = await seed(on_hand=10, reserved=2)
    r = await patch_stock(client, headers["WAREHOUSE_STAFF"], ids["item"]["id"])
    assert r.status_code == 200 and r.json()["quantity_on_hand"] == 10


@pytest.mark.parametrize(
    "body",
    [
        {"quantity_on_hand": -1},
        {"reserved_quantity": -1},
        {"quantity_on_hand": "lots"},
        {"quantity_on_hand": 1.5},
    ],
)
async def test_invalid_quantities_are_rejected_and_nothing_changes(client, headers, seed, body):
    ids = await seed(on_hand=10, reserved=2)
    r = await patch_stock(client, headers["WAREHOUSE_STAFF"], ids["item"]["id"], **body)
    assert r.status_code == 422
    after = await get_item(client, headers["ADMIN"], ids["item"]["id"])
    assert (after["quantity_on_hand"], after["reserved_quantity"]) == (10, 2)


async def test_cannot_reserve_more_than_on_hand(client, headers, seed):
    ids = await seed(on_hand=10, reserved=2)
    r = await patch_stock(client, headers["WAREHOUSE_STAFF"], ids["item"]["id"], reserved_quantity=11)
    assert r.status_code == 422
    assert "reserved_quantity" in r.json()["detail"]


async def test_cannot_reduce_on_hand_below_existing_reservations(client, headers, seed):
    ids = await seed(on_hand=10, reserved=8)
    staff = headers["WAREHOUSE_STAFF"]
    assert (await patch_stock(client, staff, ids["item"]["id"], quantity_on_hand=7)).status_code == 422
    # Reducing to exactly the reserved amount is the boundary and is allowed.
    ok = await patch_stock(client, staff, ids["item"]["id"], quantity_on_hand=8)
    assert ok.status_code == 200 and ok.json()["available_quantity"] == 0


async def test_can_set_both_fields_together_in_one_request(client, headers, seed):
    ids = await seed(on_hand=10, reserved=8)
    r = await patch_stock(client, headers["WAREHOUSE_STAFF"], ids["item"]["id"], quantity_on_hand=3, reserved_quantity=1)
    assert r.status_code == 200 and r.json()["available_quantity"] == 2


async def test_unknown_item_is_404(client, headers):
    import uuid

    r = await patch_stock(client, headers["WAREHOUSE_STAFF"], uuid.uuid4(), quantity_on_hand=5)
    assert r.status_code == 404


async def test_a_product_can_only_be_stocked_once_per_warehouse(client, headers, seed):
    ids = await seed()
    dup = {"product_id": ids["product"]["id"], "warehouse_id": ids["warehouse"]["id"]}
    assert (await client.post(f"{API}/inventory", headers=headers["INVENTORY_MANAGER"], json=dup)).status_code == 409


# ---- effects on derived figures ----------------------------------------------------------------


async def test_incrementing_stock_clears_a_low_stock_alert(client, headers, seed):
    ids = await seed(on_hand=10, reserved=0, reorder_level=20)  # 10 <= 20 -> alert
    mgr = headers["INVENTORY_MANAGER"]
    alerts = (await client.get(f"{API}/inventory/low-stock", headers=mgr)).json()
    assert [(a["sku"], a["status"]) for a in alerts] == [("A1", "critical")]  # 10*2 <= 20

    await patch_stock(client, mgr, ids["item"]["id"], quantity_on_hand=15)
    assert (await client.get(f"{API}/inventory/low-stock", headers=mgr)).json()[0]["status"] == "low"

    await patch_stock(client, mgr, ids["item"]["id"], quantity_on_hand=21)
    assert (await client.get(f"{API}/inventory/low-stock", headers=mgr)).json() == []


async def test_status_boundaries(client, headers, seed):
    """At exactly the reorder level the product is low; at half or below it is critical."""
    ids = await seed(on_hand=20, reserved=0, reorder_level=20)
    mgr, item_id = headers["INVENTORY_MANAGER"], ids["item"]["id"]
    status = lambda: client.get(f"{API}/inventory/low-stock", headers=mgr)  # noqa: E731

    assert (await status()).json()[0]["status"] == "low"  # 20 == reorder level
    await patch_stock(client, mgr, item_id, quantity_on_hand=11)
    assert (await status()).json()[0]["status"] == "low"  # 11*2 > 20
    await patch_stock(client, mgr, item_id, quantity_on_hand=10)
    assert (await status()).json()[0]["status"] == "critical"  # 10*2 == 20
    await patch_stock(client, mgr, item_id, quantity_on_hand=0)
    assert (await status()).json()[0]["status"] == "critical"


async def test_status_uses_the_product_total_across_warehouses(client, headers, seed):
    ids = await seed(on_hand=5, reserved=0, reorder_level=20, warehouse="East")
    mgr = headers["INVENTORY_MANAGER"]
    west = (await client.post(f"{API}/warehouses", headers=mgr, json={"name": "West", "location": "SF"})).json()
    other = (
        await client.post(
            f"{API}/inventory",
            headers=mgr,
            json={"product_id": ids["product"]["id"], "warehouse_id": west["id"], "quantity_on_hand": 100},
        )
    ).json()

    # 5 at East alone looks tiny, but the product has 105 in total -> not low.
    assert (await client.get(f"{API}/inventory/low-stock", headers=mgr)).json() == []
    rows = (await client.get(f"{API}/inventory?sort_by=quantity_on_hand", headers=mgr)).json()["items"]
    assert [r["product_total_quantity"] for r in rows] == [105, 105]

    # Drawing West down to 10 makes the total 15 <= 20 -> alert, even though East didn't change.
    await patch_stock(client, mgr, other["id"], quantity_on_hand=10)
    assert [a["sku"] for a in (await client.get(f"{API}/inventory/low-stock", headers=mgr)).json()] == ["A1"]


async def test_increment_is_reflected_in_summary_totals_and_value(client, headers, seed):
    ids = await seed(on_hand=10, reserved=2, reorder_level=0)  # unit price 2.50
    staff = headers["WAREHOUSE_STAFF"]
    before = (await client.get(f"{API}/inventory/summary", headers=staff)).json()
    assert (before["total_quantity_on_hand"], before["total_available_quantity"], float(before["total_value"])) == (10, 8, 25.0)

    await patch_stock(client, staff, ids["item"]["id"], quantity_on_hand=40)

    after = (await client.get(f"{API}/inventory/summary", headers=staff)).json()
    assert after["total_quantity_on_hand"] == 40
    assert after["total_available_quantity"] == 38
    assert float(after["total_value"]) == 100.0
    assert after["warehouses"][0]["quantity_on_hand"] == 40


async def test_increment_invalidates_the_cached_summary_but_a_rejected_one_does_not(client, headers, seed, redis):
    from app.services.inventory_summary import SUMMARY_CACHE_KEY

    ids = await seed(on_hand=10, reserved=2)
    staff = headers["WAREHOUSE_STAFF"]
    await client.get(f"{API}/inventory/summary", headers=staff)
    assert SUMMARY_CACHE_KEY in redis.store

    await patch_stock(client, staff, ids["item"]["id"], reserved_quantity=99)  # rejected (422)
    assert SUMMARY_CACHE_KEY in redis.store

    await patch_stock(client, staff, ids["item"]["id"], quantity_on_hand=11)  # accepted
    assert SUMMARY_CACHE_KEY not in redis.store


async def test_stock_update_still_succeeds_when_redis_is_down(client, headers, seed, redis):
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def boom(*_a, **_k):
        raise RedisConnectionError("down")

    ids = await seed(on_hand=10)
    redis.delete, redis.get, redis.set = boom, boom, boom

    staff = headers["WAREHOUSE_STAFF"]
    assert (await patch_stock(client, staff, ids["item"]["id"], quantity_on_hand=12)).status_code == 200
    # Summary degrades to the database instead of failing.
    summary = await client.get(f"{API}/inventory/summary", headers=staff)
    assert summary.status_code == 200 and summary.json()["total_quantity_on_hand"] == 12
