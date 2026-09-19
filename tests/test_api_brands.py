"""Contract tests: the brand registry endpoints."""


def _brands(api_client):
    return api_client.get("/api/brands").json()


def test_list_brands_counts_receipts_and_lists_unmatched(api_client):
    body = _brands(api_client)

    assert body["brands"], "the fixture archive has a brand registry"
    seven = next(b for b in body["brands"] if "セブン" in b["label"] or "seven" in b["id"])
    assert seven["receipt_count"] > 0 and seven["prefixes"]
    overview = body["overview"]
    assert overview["receipts"] == overview["matched"] + overview["unmatched"]
    assert sum(row["count"] for row in body["unmatched"]) == overview["unmatched"]


def test_create_update_add_prefix_and_delete(api_client, configured_archive):
    created = api_client.post("/api/brands", json={"label": "Test Brand", "prefixes": [" Testco ", "Testco Mart"]})
    assert created.status_code == 201
    brand = created.json()
    assert brand["id"] == "test-brand" and brand["prefixes"] == ["Testco", "Testco Mart"]

    renamed = api_client.patch(f"/api/brands/{brand['id']}", json={"label": "Renamed", "prefixes": ["Testco"]}).json()
    assert (renamed["id"], renamed["label"], renamed["prefixes"]) == (brand["id"], "Renamed", ["Testco"])

    with_prefix = api_client.post(f"/api/brands/{brand['id']}/prefixes", json={"prefix": "Testco Express"}).json()
    assert with_prefix["prefixes"] == ["Testco", "Testco Express"]
    assert api_client.post(f"/api/brands/{brand['id']}/prefixes", json={"prefix": "Testco"}).json()["prefixes"] \
        == ["Testco", "Testco Express"]                     # a duplicate prefix changes nothing

    assert api_client.delete(f"/api/brands/{brand['id']}").status_code == 200
    assert brand["id"] not in {b["id"] for b in _brands(api_client)["brands"]}


def test_a_new_brand_regroups_the_receipts_that_match_it(api_client):
    before = _brands(api_client)["overview"]
    target = max(_brands(api_client)["unmatched"], key=lambda row: row["count"])

    created = api_client.post("/api/brands", json={"label": "Freshly Grouped", "prefixes": [target["name"]]}).json()

    after = _brands(api_client)
    assert after["overview"]["matched"] == before["matched"] + target["count"]
    assert next(b for b in after["brands"] if b["id"] == created["id"])["receipt_count"] == target["count"]
    # the charts must see it too, not a cached frame
    merchants = api_client.get("/api/merchants", params={"group_by": "brand"}).json()
    assert any(m["brand_id"] == created["id"] for m in merchants)


def test_brands_reject_empty_values_and_unknown_ids(api_client):
    assert api_client.post("/api/brands", json={"label": "  ", "prefixes": ["x"]}).status_code == 422
    assert api_client.post("/api/brands", json={"label": "No Prefixes", "prefixes": ["  "]}).status_code == 422
    assert api_client.patch("/api/brands/nope", json={"label": "x", "prefixes": ["y"]}).status_code == 404
    assert api_client.delete("/api/brands/nope").status_code == 404


def test_suggestions_come_from_unmatched_names_and_remember_their_settings(api_client):
    loose = api_client.get("/api/brands/suggestions", params={"min_count": 1, "min_length": 2}).json()
    assert loose, "with a minimum count of 1 every unmatched name suggests its own prefix"
    assert all(row["count"] >= 1 for row in loose)

    strict = api_client.get("/api/brands/suggestions", params={"min_count": 50}).json()
    assert strict == []
    assert api_client.get("/api/config").json()["prefix_suggestion_min_count"] == 50   # remembered
