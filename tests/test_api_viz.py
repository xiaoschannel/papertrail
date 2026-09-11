"""Contract tests: visualize endpoints == fixture-backed pure-function output."""

import analytics
from api.serialization import df_records
from viz_records import build_viz_records


def test_viz_records_matches_pure_output(api_client, configured_archive):
    resp = api_client.get("/api/viz/records")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 8
    assert resp.json() == df_records(build_viz_records(configured_archive))


def test_viz_items_count(api_client):
    resp = api_client.get("/api/viz/items")
    assert resp.status_code == 200
    assert len(resp.json()) == 9


def test_top_merchants_by_spend(api_client):
    resp = api_client.get("/api/analytics/top-merchants", params={"group_by": "brand", "rank_by": "total_spend"})
    assert resp.status_code == 200
    top = resp.json()[0]
    assert top["merchant_group"] == "ビックカメラ"
    assert top["total_spend"] == 98000.0


def test_monthly_spend(api_client):
    rows = api_client.get("/api/analytics/monthly-spend").json()
    jan = next(r for r in rows if str(r["month_ts"]).startswith("2025-01"))
    assert jan["spend"] == 1900.0


def test_monthly_volume(api_client):
    rows = api_client.get("/api/analytics/monthly-volume").json()
    jan = next(r for r in rows if str(r["month_ts"]).startswith("2025-01"))
    assert jan["count"] == 2   # 品川 + 上野; undated documents have no month


def test_merchant_profile_by_brand(api_client):
    resp = api_client.get("/api/analytics/merchant", params={"brand_id": "seven-eleven"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["metrics"]["visit_count"] == 3
    assert body["metrics"]["total_spend"] == 2880.0
    assert body["trend"]  # non-empty monthly trend

    # gallery list: newest first, and slim (no OCR text or line items per receipt)
    dates = [r["date"] for r in body["receipts"]]
    assert len(dates) == 3 and dates == sorted(dates, reverse=True)
    assert not {"ocr_markdown", "items"} & set(body["receipts"][0])


def test_merchant_requires_selector(api_client):
    assert api_client.get("/api/analytics/merchant").status_code == 400


def test_timecapsule(api_client):
    rows = api_client.get("/api/analytics/timecapsule", params={"month": 11, "day": 3}).json()
    assert {r["name"] for r in rows} == {"上海小笼包馆", "スターバックス 渋谷店"}


def test_calendar_range(api_client):
    body = api_client.get("/api/analytics/calendar", params={"start": "2025-01-01", "end": "2025-02-01"}).json()
    assert "2025-01-10" in body and "2025-01-15" in body


def test_years_endpoint(api_client):
    assert api_client.get("/api/years").json() == [2025, 2024, 2023]


def test_date_range(api_client):
    body = api_client.get("/api/date-range").json()
    assert body["min"].startswith("2023-05-18")   # Tealive, earliest dated doc
    assert body["max"].startswith("2025-03-02")   # ビックカメラ, latest dated doc


def test_merchants_selector_list(api_client):
    rows = api_client.get("/api/merchants").json()
    groups = {r["merchant_group"] for r in rows}
    assert groups == {"セブン-イレブン", "ビックカメラ", "Tealive", "上海小笼包馆", "スターバックス 渋谷店"}
    seven = next(r for r in rows if r["merchant_group"] == "セブン-イレブン")
    assert seven["visit_count"] == 3


def test_year_filter_narrows_results(api_client):
    y2023 = api_client.get("/api/analytics/top-merchants", params={"year": 2023}).json()
    assert {r["merchant_group"] for r in y2023} == {"Tealive", "スターバックス 渋谷店"}

    y2025 = api_client.get("/api/analytics/top-merchants", params={"year": 2025}).json()
    assert y2025[0]["merchant_group"] == "ビックカメラ"

    for endpoint in ("monthly-spend", "monthly-volume"):
        months = api_client.get(f"/api/analytics/{endpoint}", params={"year": 2023}).json()
        assert months and all(str(m["month_ts"]).startswith("2023") for m in months)


def test_receipt_lookup_and_404(api_client):
    ok = api_client.get("/api/receipt", params={"file": "5:106-107"})
    assert ok.status_code == 200
    assert len(ok.json()["paths"]) == 2
    assert api_client.get("/api/receipt", params={"file": "nope"}).status_code == 404
