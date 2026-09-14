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


def test_patch_changes_only_the_given_fields(api_client):
    api_client.patch("/api/config", json={"dashboard_rank_by": "Visit Count"})
    patched = api_client.patch("/api/config", json={"parse_custom_instruction": "Prefer store names in Japanese."})
    assert patched.status_code == 200
    body = api_client.get("/api/config").json()
    # the second patch didn't revert the first, and untouched settings keep their values
    assert body["dashboard_rank_by"] == "Visit Count"
    assert body["parse_custom_instruction"] == "Prefer store names in Japanese."
    assert body["normalize_engine"] == "string"


def test_patch_rejects_unknown_fields(api_client):
    assert api_client.patch("/api/config", json={"not_a_setting": 1}).status_code == 422
