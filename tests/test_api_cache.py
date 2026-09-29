"""The server-side archive cache: it must avoid re-parsing, and must notice changes."""

from papertrail.api import cache
from papertrail.viz.records import build_viz_records


def test_repeated_requests_parse_archive_once(api_client, monkeypatch):
    calls = []
    real = cache.build_viz_records
    monkeypatch.setattr(cache, "build_viz_records", lambda p, state=None: calls.append(p) or real(p, state))

    for path in ("/api/years", "/api/analytics/monthly-spend",
                 "/api/analytics/top-merchants", "/api/viz/items"):
        assert api_client.get(path).status_code == 200

    assert len(calls) == 1  # four endpoints, one archive parse


def test_merchant_page_no_longer_parses_twice(api_client, monkeypatch):
    calls = []
    real = cache.build_viz_records
    monkeypatch.setattr(cache, "build_viz_records", lambda p, state=None: calls.append(p) or real(p, state))

    assert api_client.get("/api/analytics/merchant", params={"brand_id": "seven-eleven"}).status_code == 200
    assert len(calls) == 1  # records + items share one parse


def test_removing_a_document_invalidates(api_client, configured_archive):
    assert len(api_client.get("/api/viz/records").json()) == 8

    month = configured_archive / "2023" / "05"
    for f in list(month.iterdir()):  # Tealive, the only doc in 2023/05
        f.unlink()

    assert len(api_client.get("/api/viz/records").json()) == 7


def test_ttl_expiry_rebuilds(api_client, monkeypatch):
    api_client.get("/api/years")
    monkeypatch.setattr(cache, "TTL_SECONDS", -1.0)  # everything is now stale

    calls = []
    real = cache.build_viz_records
    monkeypatch.setattr(cache, "build_viz_records", lambda p, state=None: calls.append(p) or real(p, state))
    api_client.get("/api/years")
    assert len(calls) == 1


def test_cached_result_equals_fresh_build(api_client, configured_archive):
    from papertrail.api.serialization import df_records

    api_client.get("/api/years")  # warm
    assert api_client.get("/api/viz/records").json() == df_records(build_viz_records(configured_archive))


def test_a_sidebar_count_is_kept_until_its_inputs_change_or_the_cache_clears(configured_archive):
    calls = []

    def count(inputs):
        return cache.sidebar_count(configured_archive, "test", inputs, lambda: calls.append(1) or len(calls))

    assert count((1,)) == count((1,)) == 1
    assert count((2,)) == 2                     # an input changed
    cache.clear()
    assert count((2,)) == 3                     # an in-place edit cleared it


def test_a_sidebar_count_taken_across_a_clear_is_not_kept(configured_archive):
    def stale():
        cache.clear()                           # an edit lands while the count is being taken
        return "stale"

    assert cache.sidebar_count(configured_archive, "test", (), stale) == "stale"
    assert cache.sidebar_count(configured_archive, "test", (), lambda: "fresh") == "fresh"
