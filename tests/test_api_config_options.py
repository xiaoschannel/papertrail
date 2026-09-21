"""Contract tests: the Config page's option lists and folder check."""

from api import ingest_registry


def test_options_lists_models_engines_and_schemes(api_client, monkeypatch):
    monkeypatch.setattr(ingest_registry, "ocr_providers", lambda: {"Fake OCR": object()})
    monkeypatch.setattr(ingest_registry, "extractors", lambda: {"Fake LLM": lambda *a, **k: None})

    body = api_client.get("/api/config/options").json()

    assert body["ocr_models"] == ["Fake OCR"] and body["extractors"] == ["Fake LLM"]
    assert {e["id"] for e in body["normalize_engines"]} == {"embedding", "string"}
    assert all(e["label"] for e in body["normalize_engines"])
    assert "Canon ImageFormula" in body["indexing_schemes"]
    assert body["dashboard_rank_by"] == ["Total Spend", "Visit Count"]
    assert 0 < body["embedding_threshold_step"] < 0.05
    assert {" ", "Enter", "Escape", "ArrowLeft"} <= set(body["shortcut_named_keys"])
    assert "Tab" not in body["shortcut_named_keys"]


def test_path_check(api_client, tmp_path, configured_archive):
    existing = api_client.get("/api/config/path-check", params={"path": str(configured_archive)}).json()
    assert (existing["exists"], existing["is_dir"]) == (True, True)

    missing = api_client.get("/api/config/path-check", params={"path": str(tmp_path / "nope")}).json()
    assert (missing["exists"], missing["is_dir"]) == (False, False)

    a_file = tmp_path / "a.txt"
    a_file.write_text("x", encoding="utf-8")
    assert api_client.get("/api/config/path-check", params={"path": str(a_file)}).json() == {
        "path": str(a_file), "exists": True, "is_dir": False}
