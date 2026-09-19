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


def _months(rows):
    return [str(r["month_ts"])[:7] for r in rows]


def _assert_contiguous(rows):
    import pandas as pd

    months = _months(rows)
    expected = [str(p) for p in pd.period_range(months[0], months[-1], freq="M")]
    assert months == expected, "a monthly series skipped months — time would be compressed"


def test_monthly_series_keep_time_gaps(api_client):
    # REQUIREMENT: empty months must be present so gaps (e.g. an archiving pause)
    # show as empty space instead of silently collapsing on the chart.
    # Fixture documents span 2023-05 .. 2025-03 with long empty stretches.
    spend = api_client.get("/api/analytics/monthly-spend").json()
    volume = api_client.get("/api/analytics/monthly-volume").json()
    for series in (spend, volume):
        _assert_contiguous(series)
        assert _months(series)[0] == "2023-05" and _months(series)[-1] == "2025-03"
        assert len(series) == 23

    gap_month = next(r for r in spend if _months([r])[0] == "2024-06")
    assert gap_month["spend"] == 0
    assert next(r for r in volume if _months([r])[0] == "2024-06")["count"] == 0


def test_dashboard_charts_share_one_timeline(api_client, configured_archive):
    # Side-by-side charts must put the same month at the same position, even when
    # non-receipt documents extend the timeline beyond the first/last receipt.
    from models import ReviewDecision, Sidecar

    early = configured_archive / "2022" / "01"
    early.mkdir(parents=True)
    (early / "2022年1月15日 10：00 年金定期便.json").write_text(Sidecar(
        original_filename="01152022100000_900.png", batch_id=9, serial=900,
        review=ReviewDecision(verdict="accepted", document_type="other",
                              name="年金定期便", date="2022-01-15", time="10:00"),
    ).model_dump_json(), encoding="utf-8")

    spend = api_client.get("/api/analytics/monthly-spend").json()
    volume = api_client.get("/api/analytics/monthly-volume").json()
    assert _months(volume)[0] == "2022-01"          # the non-receipt starts the timeline
    assert _months(spend) == _months(volume)        # spend is padded onto the same one
    assert spend[0]["spend"] == 0

    for params in ({"year": 2023}, {"year": 2025}):
        spend = api_client.get("/api/analytics/monthly-spend", params=params).json()
        volume = api_client.get("/api/analytics/monthly-volume", params=params).json()
        assert _months(spend) == _months(volume), f"timelines differ for {params}"


def test_merchant_trend_keeps_time_gaps(api_client):
    trend = api_client.get("/api/analytics/merchant", params={"brand_id": "seven-eleven"}).json()["trend"]
    _assert_contiguous(trend)


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


# --- editing an archived document -----------------------------------------------------------
def _first_record(api_client):
    return api_client.get("/api/viz/records").json()[0]


def test_edit_receipt_refiles_and_returns_the_updated_record(api_client, configured_archive):
    record = _first_record(api_client)
    body = {"file": record["filename"], "document_type": record["document_type"], "name": "Edited Shop",
            "date": record["date"], "time": record["time"], "cost": 42.0, "currency": record["currency"],
            "address": record["address"], "language": record["language"], "comment": "fixed the total"}

    updated = api_client.patch("/api/receipt", json=body).json()

    assert updated["name"] == "Edited Shop" and updated["cost"] == 42.0
    assert updated["comment"] == "fixed the total"
    assert "Edited Shop" in updated["path"] and (configured_archive / updated["path"]).exists()
    assert not (configured_archive / record["path"]).exists()
    # the visualize pages see the change immediately (server cache cleared)
    fresh = next(r for r in api_client.get("/api/viz/records").json() if r["filename"] == record["filename"])
    assert (fresh["name"], fresh["cost"], fresh["path"]) == ("Edited Shop", 42.0, updated["path"])


def test_edit_receipt_rejects_bad_values_and_unknown_documents(api_client):
    record = _first_record(api_client)
    body = {"file": record["filename"], "document_type": "receipt", "name": "x", "date": "nope",
            "time": record["time"], "cost": 1.0, "currency": "JPY"}
    refused = api_client.patch("/api/receipt", json=body)
    assert refused.status_code == 422 and "Date" in refused.json()["detail"]

    assert api_client.patch("/api/receipt", json={**body, "file": "no-such-doc", "date": record["date"]}).status_code == 404

    no_cost = api_client.patch("/api/receipt", json={**body, "date": record["date"], "cost": None})
    assert no_cost.status_code == 422 and no_cost.json()["detail"] == "Receipt requires: cost"


def test_merchant_profile_lists_brand_locations(api_client):
    merchants = api_client.get("/api/merchants", params={"group_by": "brand"}).json()
    brand = next(m for m in merchants if m["brand_id"])
    profile = api_client.get("/api/analytics/merchant", params={"brand_id": brand["brand_id"]}).json()

    receipts = api_client.get("/api/analytics/merchant", params={"brand_id": brand["brand_id"]}).json()["receipts"]
    expected: dict[str, int] = {}
    for receipt in receipts:
        expected[receipt["brand_location"] or "(no remainder)"] = expected.get(receipt["brand_location"] or "(no remainder)", 0) + 1
    assert {row["location"]: row["count"] for row in profile["locations"]} == expected
    assert sum(row["count"] for row in profile["locations"]) == profile["metrics"]["visit_count"]
