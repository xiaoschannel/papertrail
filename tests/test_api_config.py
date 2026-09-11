"""Contract tests for the config router."""


def test_get_config_reflects_fixture(api_client, configured_archive):
    resp = api_client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["batch_output_path"] == str(configured_archive)


def test_put_config_persists(api_client):
    cfg = api_client.get("/api/config").json()
    cfg["dashboard_rank_by"] = "Visit Count"
    put = api_client.put("/api/config", json=cfg)
    assert put.status_code == 200
    assert put.json()["dashboard_rank_by"] == "Visit Count"
    assert api_client.get("/api/config").json()["dashboard_rank_by"] == "Visit Count"


def test_health(api_client):
    assert api_client.get("/api/health").json() == {"status": "ok"}
