"""Contract tests: the Slice endpoints, and how every other step treats a sliced sheet and its crops."""

import threading

import pytest
from PIL import Image

from api import ingest_registry
from data import load_decisions, save_decisions
from models import load_scan_index
from tilt_scans import scan as tilt_fixture

THREE = {"frame": {"x1": 0, "y1": 0, "x2": 1000, "y2": 1000}, "row_lines": [500], "col_lines": [500],
         "cells": [[1, 1], [1, 2], [2, 1]]}
CROP = "slices/01102025133000_2.r1c2.500-0-1000-500.png"      # 1:10, the second receipt on sheet 1:2


@pytest.fixture
def scans(configured_ingest, tmp_path):
    folder = tmp_path / "scans"
    for _, fn in sorted(load_scan_index(configured_ingest).batches[0].files.items()):
        Image.new("RGB", (100, 200), "white").save(folder / fn)
    return folder


def slice_sheet(client, key="1:2", grid=THREE):
    plan = client.post("/api/ingest/slices/plan", json={"key": key, "grid": grid})
    assert plan.status_code == 200, plan.text
    applied = client.put("/api/ingest/slices", json={"key": key, "grid": grid, "token": plan.json()["token"]})
    assert applied.status_code == 200, applied.text
    return applied.json()


def test_the_slice_page_lists_scanned_pages_and_why_some_cant_be_sliced(ingest_client, scans):
    body = ingest_client.get("/api/ingest/slicing").json()
    sheets = {s["key"]: s for s in body["sheets"]}
    assert list(sheets) == [f"1:{i}" for i in range(1, 9)] and body["problems"] == []
    assert "multi-page" in sheets["1:4"]["refusal"] and "recover it" in sheets["1:6"]["refusal"]
    assert sheets["1:2"]["refusal"] is None and sheets["1:2"]["grid"] is None and sheets["1:2"]["image_available"]


def test_slicing_a_sheet_through_the_api(ingest_client, configured_ingest, scans):
    plan = ingest_client.post("/api/ingest/slices/plan", json={"key": "1:2", "grid": THREE}).json()
    assert plan["new_keys"] == ["1:9", "1:10", "1:11"] and plan["moved"] == [] and plan["replaces_decision"]
    applied = slice_sheet(ingest_client)
    assert applied["new_keys"] == plan["new_keys"]

    sheet = next(s for s in ingest_client.get("/api/ingest/slicing").json()["sheets"] if s["key"] == "1:2")
    assert sheet["sliced"] and sheet["grid"]["cells"] == [[1, 1], [1, 2], [2, 1]]
    assert [(c["key"], c["row"], c["col"], c["image_available"]) for c in sheet["crops"]] == [
        ("1:9", 1, 1, True), ("1:10", 1, 2, True), ("1:11", 2, 1, True)]

    pages = {p["key"]: p for p in ingest_client.get("/api/ingest/grouping").json()["pages"]}
    assert pages["1:2"]["sliced"] and pages["1:2"]["tossed"]
    assert (pages["1:10"]["crop_of"], pages["1:10"]["cell"], pages["1:10"]["filename"]) == ("1:2", [1, 2], CROP)

    # the crop's image is served from its slices/ folder like any scan, with the / escaped as the web app
    # escapes it (encodeURIComponent) or not
    for name in (CROP, CROP.replace("/", "%2F")):
        assert ingest_client.get(f"/api/media/input/{name}").status_code == 200
        thumb = ingest_client.get(f"/api/media/input-thumb/{name}", params={"width": 64})
        assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"


def test_a_refused_or_stale_slice(ingest_client, scans):
    refused = ingest_client.post("/api/ingest/slices/plan", json={"key": "1:4", "grid": THREE})
    assert refused.status_code == 422 and "multi-page" in refused.json()["detail"]
    bad_grid = {**THREE, "cells": []}
    assert ingest_client.post("/api/ingest/slices/plan", json={"key": "1:2", "grid": bad_grid}).status_code == 422
    assert ingest_client.post("/api/ingest/slices/plan", json={"key": "9:1", "grid": THREE}).status_code == 404
    assert ingest_client.post("/api/ingest/slices/plan", json={"key": "nope", "grid": THREE}).status_code == 404

    stale = ingest_client.post("/api/ingest/slices/plan", json={"key": "1:2", "grid": THREE}).json()["token"]
    slice_sheet(ingest_client, "1:3", {**THREE, "cells": [[1, 1]]})
    again = ingest_client.put("/api/ingest/slices", json={"key": "1:2", "grid": THREE, "token": stale})
    assert again.status_code == 409


def test_slicing_waits_for_a_job_on_its_batch(ingest_client, scans, monkeypatch):
    from tests.test_api_ingest import GatedOcr, _finish

    provider = GatedOcr(threading.Event())
    monkeypatch.setattr(ingest_registry, "ocr_providers", lambda: {"Fake OCR": provider})
    plan = ingest_client.post("/api/ingest/slices/plan", json={"key": "1:2", "grid": THREE}).json()
    job = ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "batch_id": 1, "reprocess": True,
                                                      "limit": 1}).json()
    assert provider.started.wait(10)
    held = ingest_client.put("/api/ingest/slices", json={"key": "1:2", "grid": THREE, "token": plan["token"]})
    assert held.status_code == 409 and "using batch 1" in held.json()["detail"]
    provider.gate.set()
    _finish(job)


def test_nothing_but_unslicing_takes_back_a_sliced_toss(ingest_client, configured_ingest, scans):
    slice_sheet(ingest_client)
    for path in ("/api/ingest/pages/recover", "/api/ingest/pages/toss"):
        refused = ingest_client.post(path, json={"key": "1:2"})
        assert refused.status_code == 409 and "Slice page" in refused.json()["detail"]
    sliced = load_decisions(configured_ingest)["1:2"].model_dump()
    draft = {"document_type": "other", "name": "", "date": "", "time": "", "cost": None, "currency": ""}
    undo = ingest_client.post("/api/review/undo", json={"key": "1:2", "verdict": "tossed", "draft": draft})
    assert undo.status_code == 409 and "sliced sheet" in undo.json()["detail"] and sliced["toss_reason"] == "sliced"

    assert ingest_client.delete("/api/review/decisions").status_code == 200
    assert list(load_decisions(configured_ingest)) == ["1:2"]              # clearing all keeps it

    unsliced = slice_sheet(ingest_client, grid=None)
    assert unsliced["new_keys"] == [] and "1:2" not in load_decisions(configured_ingest)


def test_review_cant_decide_a_sliced_sheet(ingest_client, configured_ingest, scans):
    """Slicing drops what Parse made of the sheet, so Review has nothing to show; were an extraction to
    come back, a decision on it would still be refused."""
    slice_sheet(ingest_client)
    from data import load_extractions, merge_extractions
    merge_extractions(configured_ingest, {"1:2": next(iter(load_extractions(configured_ingest).values()))})
    from api import ingest_store
    ingest_store.clear()
    draft = {"document_type": "other", "name": "x", "date": "", "time": "", "cost": None, "currency": ""}
    decided = ingest_client.post("/api/review/decisions", json={"key": "1:2", "verdict": "marked", "draft": draft})
    assert decided.status_code == 409 and load_decisions(configured_ingest)["1:2"].sliced


def test_crops_and_sliced_sheets_cant_be_rotated_and_crops_cant_be_linked(ingest_client, scans):
    slice_sheet(ingest_client)
    for key in ("1:2", "1:10"):
        refused = ingest_client.post("/api/ingest/pages/rotate", json={"key": key, "top_points": "left"})
        assert refused.status_code == 409
    linked = ingest_client.put("/api/ingest/grouping", json={"batch_id": 1, "groups": [["1:9", "1:10"]]})
    assert linked.status_code == 422 and "crop" in linked.json()["detail"]
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:10"}).status_code == 200   # a crop can go


def test_only_pages_the_rotate_arrows_can_turn_are_checked_for_orientation(ingest_client, scans):
    """A sheet is turned before it is cut, so its crops come out the way it faced: neither a sliced sheet
    nor its crops can be rotated, and neither is suggested."""
    from tests.test_api_ingest import printed

    for name in ("01102025133000_2.png", "01102025140000_3.png"):                  # 1:2, the sheet, and 1:3
        printed(top_points="down").resize((600, 1320)).save(scans / name)
    turned = lambda: [p["key"] for p in ingest_client.get("/api/ingest/turned", params={"batch_id": 1}).json()["pages"]]
    assert turned() == ["1:2", "1:3"]
    slice_sheet(ingest_client)                                                     # crops 1:9-1:11, cut upside down
    assert turned() == ["1:3"]


def test_crops_and_sliced_sheets_are_neither_suggested_for_straightening_nor_straightened(ingest_client, scans):
    slice_sheet(ingest_client)
    for filename in ("01102025133000_2.png", CROP, "01102025140000_3.png"):   # sheet 1:2, crop 1:10, page 1:3
        tilt_fixture("receipt_crooked.png").save(scans / filename)
    tilted = ingest_client.get("/api/ingest/tilted", params={"batch_id": 1}).json()["pages"]
    assert [p["key"] for p in tilted] == ["1:3"]
    for key, why in (("1:2", "sliced"), ("1:10", "crop")):
        refused = ingest_client.post("/api/ingest/pages/straighten", json={"key": key, "degrees": 3.7})
        assert refused.status_code == 409 and why in refused.json()["detail"]
    assert ingest_client.post("/api/ingest/pages/straighten", json={"key": "1:3", "degrees": 3.7}).status_code == 200


def test_archived_tossed_and_marked_crops_are_known_by_their_scan_names(ingest_client, configured_ingest, scans):
    """Sanity Check and a resumed Archive find a crop filed in tossed/ or marked/ under its batch name."""
    import ingest_pipeline as ip
    from data import scan_organized_filenames
    from tests.test_slicing import _Progress

    slice_sheet(ingest_client)
    decisions = load_decisions(configured_ingest)
    for key, verdict in (("1:9", "tossed"), ("1:10", "marked"), ("1:11", "tossed")):
        decisions[key] = decisions["1:6"].model_copy(update={"verdict": verdict})
    save_decisions(configured_ingest, decisions)
    ip.run_archive(configured_ingest, scans, _Progress())

    batch = load_scan_index(configured_ingest).batches[0]
    assert {batch.files[s] for s in (9, 10, 11)} <= scan_organized_filenames(configured_ingest)

    # the Workshop, working on the marked crop, sees where its batch-mates went and which one it is
    from api import cache
    cache.clear()
    scans_in_batch = ingest_client.get("/api/curate/workshop/context", params={"key": "1:10"}).json()["batch"]
    by_name = {scan["filename"]: scan for scan in scans_in_batch}
    marked, tossed = by_name[batch.files[10]], by_name[batch.files[9]]
    assert (marked["verdict"], marked["current"], tossed["verdict"]) == ("marked", True, "tossed")
    assert marked["image"].endswith(batch.files[10].removeprefix("slices/"))


def test_index_audit_counts_crops_as_on_disk(ingest_client, scans):
    slice_sheet(ingest_client)
    audit = ingest_client.get("/api/dev/index-audit").json()
    assert audit["input"]["indexed_not_on_disk"] == 0 and audit["input"]["on_disk_not_indexed"] == []


def test_an_interrupted_slice_blocks_archive_until_it_is_repaired(ingest_client, configured_ingest, scans):
    decisions = load_decisions(configured_ingest)
    decisions["1:3"] = decisions["1:3"].model_copy(update={"verdict": "tossed", "toss_reason": "sliced"})
    save_decisions(configured_ingest, decisions)

    assert ingest_client.get("/api/ingest/slicing").json()["problems"] == ["1:3 is tossed as sliced but has no grid"]
    blocked = ingest_client.get("/api/ingest/archive").json()["blocker"]
    assert blocked.startswith("Fix the sliced sheets on the Slice page first")
    assert ingest_client.get("/api/ingest/counts").json()["archive"] == 0      # the sidebar agrees
    slice_sheet(ingest_client, "1:3", grid=None)
    assert ingest_client.get("/api/ingest/archive").json()["blocker"] == "Review all files before archiving."
