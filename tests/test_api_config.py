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


def test_config_changes_made_together_all_land_and_a_reader_never_sees_half_a_file(tmp_path, monkeypatch):
    import threading

    import settings

    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    settings.save_config(settings.AppConfig())
    failures = []

    def read():
        for _ in range(300):
            try:
                settings.get_config()
            except Exception as exc:          # a half-written file fails to parse
                failures.append(exc)

    def write(field, values):
        for value in values:
            settings.update_config(**{field: value})

    threads = [threading.Thread(target=read),
               threading.Thread(target=write, args=("ocr_model", [f"ocr {n}" for n in range(150)])),
               threading.Thread(target=write, args=("extractor_model", [f"llm {n}" for n in range(150)]))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    final = settings.get_config()
    assert (final.ocr_model, final.extractor_model) == ("ocr 149", "llm 149")


def test_only_pages_on_this_machine_are_served(api_client):
    assert api_client.get("/api/health").status_code == 200
    # a site whose name was made to resolve to 127.0.0.1 still sends its own name as Host
    assert api_client.get("/api/health", headers={"host": "rebound.example:8000"}).status_code == 403
    # a script on another site sends its own Origin
    assert api_client.patch("/api/config", json={"ocr_model": "x"},
                            headers={"origin": "https://evil.example"}).status_code == 403
    # a form or image on another site's page
    assert api_client.post("/api/ingest/archive", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    # the web app itself, through the dev server on another local port
    assert api_client.get("/api/health", headers={"origin": "http://localhost:5173",
                                                  "sec-fetch-site": "same-origin"}).status_code == 200



def test_shortcuts_default_to_the_left_hand(api_client):
    assert api_client.get("/api/config").json()["shortcuts"] == {
        "accept": "a", "mark": "s", "toss": "d", "prev": "x", "next": "c", "undo": "z",
        "quick_1": "1", "quick_2": "2", "quick_3": "3", "hide_boxes": "b", "hold_original": "r", "confirm": "e", "cancel": "q",
        "leave_as_is": "w", "turn_upright": "e", "toggle_guides": "r", "straighten": "d"}


def test_shortcuts_are_saved_as_a_section(api_client):
    keys = api_client.get("/api/config").json()["shortcuts"]
    chosen = {**keys, "accept": "W", "prev": "ArrowLeft", "confirm": "Enter", "cancel": "a", "hold_original": " ",
              "quick_1": "4", "quick_2": "f"}
    assert api_client.patch("/api/config", json={"shortcuts": chosen}).status_code == 200
    body = api_client.get("/api/config").json()
    # a character is stored lower-cased, as the pages match keys; named keys as they are named
    assert body["shortcuts"] == {**chosen, "accept": "w"}
    # and the rest of the config is untouched
    assert body["normalize_engine"] == "string"


def test_shortcuts_reject_what_a_page_could_not_tell_apart(api_client):
    keys = api_client.get("/api/config").json()["shortcuts"]
    for bad in ({"mark": "a"}, {"cancel": "e"}, {"toss": "1"}, {"quick_3": "r"}, {"hold_original": "a"}, {"prev": ""}, {"next": "cc"}, {"undo": "Tab"},
                {"hide_boxes": "Shift"}, {"confirm": "enter"}, {"hold_original": "b"}):
        assert api_client.patch("/api/config", json={"shortcuts": {**keys, **bad}}).status_code == 422, bad
    assert api_client.get("/api/config").json()["shortcuts"] == keys


def test_a_config_file_naming_only_some_shortcuts_keeps_the_defaults_for_the_rest(tmp_path, monkeypatch):
    import json

    import settings

    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"shortcuts": {"accept": "f"}}), encoding="utf-8")
    keys = settings.get_config().shortcuts
    assert (keys.accept, keys.mark, keys.cancel) == ("f", "s", "q")


def test_a_patch_changes_only_the_shortcuts_it_sends_and_judges_clashes_against_the_saved_ones(api_client):
    assert api_client.patch("/api/config", json={"shortcuts": {"accept": "w"}}).status_code == 200
    assert api_client.patch("/api/config", json={"shortcuts": {"mark": "k"}}).status_code == 200
    keys = api_client.get("/api/config").json()["shortcuts"]
    assert (keys["accept"], keys["mark"], keys["toss"]) == ("w", "k", "d")
    # A is free now that accept is on W; D is still toss's
    assert api_client.patch("/api/config", json={"shortcuts": {"mark": "a"}}).status_code == 200
    clash = api_client.patch("/api/config", json={"shortcuts": {"mark": "d"}})
    assert clash.status_code == 422 and "toss" in str(clash.json())
    assert api_client.patch("/api/config", json={"shortcuts": {"not_an_action": "y"}}).status_code == 422
    assert api_client.get("/api/config").json()["shortcuts"]["mark"] == "a"


def test_a_saved_shortcuts_section_that_no_longer_validates_falls_back_to_its_defaults(tmp_path, monkeypatch):
    import json

    import settings

    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({
        "dashboard_rank_by": "Visit Count", "shortcuts": {"accept": "s", "mark": "s"}}), encoding="utf-8")
    cfg = settings.get_config()
    # the rest of the config still reads, and the page that fixes the section can load
    assert cfg.dashboard_rank_by == "Visit Count"
    assert (cfg.shortcuts.accept, cfg.shortcuts.mark) == ("a", "s")
