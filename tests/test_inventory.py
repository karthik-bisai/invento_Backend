import json

from app.services.inventory_summary import SUMMARY_CACHE_KEY, SUMMARY_TTL_SECONDS

PREFIX = "/api/v1"


async def _seed(client, h):
    p = (await client.post(f"{PREFIX}/products", headers=h["INVENTORY_MANAGER"],
         json={"sku": "A1", "name": "Widget", "category": "parts", "unit_price": "2.50", "reorder_level": 20})).json()
    w = (await client.post(f"{PREFIX}/warehouses", headers=h["INVENTORY_MANAGER"],
         json={"name": "Main", "location": "NYC"})).json()
    i = (await client.post(f"{PREFIX}/inventory", headers=h["INVENTORY_MANAGER"],
         json={"product_id": p["id"], "warehouse_id": w["id"], "quantity_on_hand": 10, "reserved_quantity": 2})).json()
    return p, w, i


async def test_requires_auth(client):
    assert (await client.get(f"{PREFIX}/products")).status_code == 401


async def test_staff_cannot_create_product(client, headers):
    body = {"sku": "S", "name": "n", "category": "c", "unit_price": "1"}
    assert (await client.post(f"{PREFIX}/products", headers=headers["WAREHOUSE_STAFF"], json=body)).status_code == 403
    assert (await client.post(f"{PREFIX}/products", headers=headers["INVENTORY_MANAGER"], json=body)).status_code == 201


async def test_staff_updates_stock_but_cannot_create_or_delete(client, headers):
    p, w, i = await _seed(client, headers)
    staff = headers["WAREHOUSE_STAFF"]
    r = await client.patch(f"{PREFIX}/inventory/{i['id']}", headers=staff, json={"quantity_on_hand": 50})
    assert r.status_code == 200 and r.json()["available_quantity"] == 48
    new = {"product_id": p["id"], "warehouse_id": w["id"]}
    assert (await client.post(f"{PREFIX}/inventory", headers=staff, json=new)).status_code == 403
    assert (await client.delete(f"{PREFIX}/inventory/{i['id']}", headers=staff)).status_code == 403
    assert (await client.delete(f"{PREFIX}/inventory/{i['id']}", headers=headers["ADMIN"])).status_code == 204


async def test_manager_adjusts_reorder_level_only_admin_edits_product(client, headers):
    p, _, _ = await _seed(client, headers)
    mgr, admin = headers["INVENTORY_MANAGER"], headers["ADMIN"]
    r = await client.patch(f"{PREFIX}/products/{p['id']}/reorder-level", headers=mgr, json={"reorder_level": 5})
    assert r.json()["reorder_level"] == 5
    assert (await client.patch(f"{PREFIX}/products/{p['id']}", headers=mgr, json={"name": "x"})).status_code == 403
    assert (await client.patch(f"{PREFIX}/products/{p['id']}", headers=admin, json={"name": "x"})).json()["name"] == "x"


async def test_validation_and_conflicts(client, headers):
    p, w, i = await _seed(client, headers)
    mgr, staff = headers["INVENTORY_MANAGER"], headers["WAREHOUSE_STAFF"]
    dup = {"sku": "A1", "name": "d", "category": "c", "unit_price": "1"}
    assert (await client.post(f"{PREFIX}/products", headers=mgr, json=dup)).status_code == 409
    again = {"product_id": p["id"], "warehouse_id": w["id"]}
    assert (await client.post(f"{PREFIX}/inventory", headers=mgr, json=again)).status_code == 409
    url = f"{PREFIX}/inventory/{i['id']}"
    assert (await client.patch(url, headers=staff, json={"reserved_quantity": 99})).status_code == 422
    assert (await client.patch(url, headers=staff, json={"quantity_on_hand": -1})).status_code == 422
    assert (await client.delete(f"{PREFIX}/products/{p['id']}", headers=headers["ADMIN"])).status_code == 409


async def test_pagination_and_filtering(client, headers):
    mgr = headers["INVENTORY_MANAGER"]
    for n in range(5):
        cat = "even" if n % 2 == 0 else "odd"
        body = {"sku": f"S{n}", "name": f"P{n}", "category": cat, "unit_price": "1"}
        await client.post(f"{PREFIX}/products", headers=mgr, json=body)
    r = (await client.get(f"{PREFIX}/products?page=2&size=2", headers=mgr)).json()
    assert r["total"] == 5 and len(r["items"]) == 2 and r["page"] == 2
    assert (await client.get(f"{PREFIX}/products?category=even", headers=mgr)).json()["total"] == 3
    assert (await client.get(f"{PREFIX}/products?q=s4", headers=mgr)).json()["total"] == 1
    assert (await client.get(f"{PREFIX}/products?size=1000", headers=mgr)).status_code == 422


async def test_inventory_list_is_joined_searchable_sortable_and_filterable(client, headers):
    mgr = headers["INVENTORY_MANAGER"]
    w = [(await client.post(f"{PREFIX}/warehouses", headers=mgr, json={"name": n, "location": "x"})).json() for n in ("East", "West")]
    specs = [("AAA", "Alpha", 10, [5, 3]), ("BBB", "Bravo", 10, [50, 40])]  # Alpha total 8 <= 10 -> low; Bravo 90 -> ok
    for sku, name, reorder, qtys in specs:
        p = (await client.post(f"{PREFIX}/products", headers=mgr, json={"sku": sku, "name": name, "category": "c", "unit_price": "1", "reorder_level": reorder})).json()
        for wh, qty in zip(w, qtys):
            await client.post(f"{PREFIX}/inventory", headers=mgr, json={"product_id": p["id"], "warehouse_id": wh["id"], "quantity_on_hand": qty})

    page = (await client.get(f"{PREFIX}/inventory?sort_by=quantity_on_hand&order=desc&size=2", headers=mgr)).json()
    assert page["total"] == 4 and [r["quantity_on_hand"] for r in page["items"]] == [50, 40]
    first = page["items"][0]
    assert first["sku"] == "BBB" and first["product_name"] == "Bravo" and first["warehouse_name"] == "East"
    assert first["product_total_quantity"] == 90 and first["available_quantity"] == 50

    assert (await client.get(f"{PREFIX}/inventory?q=alp", headers=mgr)).json()["total"] == 2
    low = (await client.get(f"{PREFIX}/inventory?low_stock=true", headers=mgr)).json()
    assert low["total"] == 2 and {r["sku"] for r in low["items"]} == {"AAA"}
    assert (await client.get(f"{PREFIX}/inventory?warehouse_id={w[1]['id']}", headers=mgr)).json()["total"] == 2
    assert (await client.get(f"{PREFIX}/inventory?sort_by=bogus", headers=mgr)).status_code == 422


async def test_summary_values(client, headers):
    await _seed(client, headers)
    s = (await client.get(f"{PREFIX}/inventory/summary", headers=headers["WAREHOUSE_STAFF"])).json()
    assert s["total_quantity_on_hand"] == 10 and s["total_reserved_quantity"] == 2
    assert s["total_available_quantity"] == 8 and float(s["total_value"]) == 25.0
    assert s["low_stock_products"] == 1 and s["warehouses"][0]["name"] == "Main"


async def test_low_stock_list_and_zero_reorder_level_ignored(client, headers):
    mgr = headers["INVENTORY_MANAGER"]
    _, _, item = await _seed(client, headers)  # 10 on hand vs reorder 20 -> half or less -> critical
    await client.post(f"{PREFIX}/products", headers=mgr, json={"sku": "NR", "name": "NoThreshold", "category": "c", "unit_price": "1"})
    rows = (await client.get(f"{PREFIX}/inventory/low-stock", headers=mgr)).json()
    assert [(r["sku"], r["status"]) for r in rows] == [("A1", "critical")]
    await client.patch(f"{PREFIX}/inventory/{item['id']}", headers=mgr, json={"quantity_on_hand": 15})
    assert (await client.get(f"{PREFIX}/inventory/low-stock", headers=mgr)).json()[0]["status"] == "low"
    await client.patch(f"{PREFIX}/inventory/{item['id']}", headers=mgr, json={"quantity_on_hand": 21})
    assert (await client.get(f"{PREFIX}/inventory/low-stock", headers=mgr)).json() == []


async def test_summary_cached_60s_and_invalidated_on_update(client, headers, redis):
    _, _, i = await _seed(client, headers)
    staff = headers["WAREHOUSE_STAFF"]
    await client.get(f"{PREFIX}/inventory/summary", headers=staff)
    assert redis.ttls[SUMMARY_CACHE_KEY] == SUMMARY_TTL_SECONDS == 60

    # Poison the cache: a hit must be served from Redis, not recomputed.
    cached = json.loads(redis.store[SUMMARY_CACHE_KEY])
    cached["total_quantity_on_hand"] = 777
    redis.store[SUMMARY_CACHE_KEY] = json.dumps(cached)
    assert (await client.get(f"{PREFIX}/inventory/summary", headers=staff)).json()["total_quantity_on_hand"] == 777

    await client.patch(f"{PREFIX}/inventory/{i['id']}", headers=staff, json={"quantity_on_hand": 30})
    assert SUMMARY_CACHE_KEY not in redis.store
    assert (await client.get(f"{PREFIX}/inventory/summary", headers=staff)).json()["total_quantity_on_hand"] == 30
