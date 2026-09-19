"""Contract tests: the Marked Workshop — documents, the treated scan, reprocess and the verdict."""

import io
import json
import shutil

import pytest
from PIL import Image

from api import ingest_registry
from api.jobs import runner
from api.model_manager import models
from data import read_sidecar
from models import ReceiptResult


@pytest.fixture
def fake_models(monkeypatch):
    class FakeOcr:
        grounding = True
        teardowns = 0

        def run(self, path, structured=False):
            return "<|ref|>合計<|/ref|><|det|>[[1, 2, 3, 4]]<|/det|>" if structured else f"re-read {path.name}"

        def teardown(self):
            FakeOcr.teardowns += 1

    def fake_extract(text, has_boxes=False, custom_instruction=""):
        return ReceiptResult(document_type="receipt", language="ja", date="2025-08-10", time="14:20",
                             name="ローソン 池袋店 (rescued)", currency="JPY", address="", cost=300.0)

    monkeypatch.setattr(ingest_registry, "ocr_providers", lambda: {"Fake OCR": FakeOcr()})
    monkeypatch.setattr(ingest_registry, "extractors", lambda: {"Fake LLM": fake_extract})
    monkeypatch.setattr(ingest_registry, "unload_extractor", lambda name: (lambda: None))


def _two_page_marked(archive_dir):
    marked = archive_dir / "marked"
    sidecar_path = marked / "08102025142000_202.json"
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    data["document_key"] = "9:202-203"
    sidecar_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    shutil.copy(marked / "08102025142000_202.png", marked / "08102025142100_203.png")
    (marked / "08102025142100_203.json").write_text(
        json.dumps(data | {"original_filename": "08102025142100_203.png", "serial": 203}, ensure_ascii=False),
        encoding="utf-8")
    return "9:202-203"


def test_the_queue_lists_documents_and_opens_the_first(api_client, configured_archive, fake_models):
    key = _two_page_marked(configured_archive)

    body = api_client.get("/api/curate/workshop").json()

    assert [d["key"] for d in body["documents"]] == [key]                 # one entry, two pages (issue #14)
    assert body["documents"][0]["pages"] == ["08102025142000_202.png", "08102025142100_203.png"]
    document = body["document"]
    assert document["key"] == key and len(document["pages"]) == 2
    assert document["defaults"]["name"] == "ローソン 池袋店"                # from the review, no extraction yet
    assert document["decision"]["verdict"] == "marked"
    assert "合計" in document["ocr_text"]


def test_the_scan_preview_is_the_treated_image(api_client, configured_archive):
    with Image.open(configured_archive / "marked" / "08102025142000_202.png") as original:
        width, height = original.size

    plain = api_client.get("/api/curate/workshop/scan", params={"filename": "08102025142000_202.png"})
    rotated = api_client.get("/api/curate/workshop/scan",
                             params={"filename": "08102025142000_202.png", "top_points": "left",
                                     "treatment": "contrast"})

    assert plain.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(plain.content)).size == (width, height)
    assert Image.open(io.BytesIO(rotated.content)).size == (height, width)
    # a file that really exists, one level up: the guard, not a missing file, is what refuses it
    assert api_client.get("/api/curate/workshop/scan",
                          params={"filename": "../brand_directory.json"}).status_code == 404


def _reread(api_client, top_points="right"):
    job = api_client.post("/api/curate/workshop/reprocess", json={
        "key": "9:202", "ocr_model": "Fake OCR", "extractor": "Fake LLM",
        "top_points": top_points, "treatment": "clahe"}).json()
    assert job["cancellable"] is False                  # two model calls: nothing to stop between
    return runner.wait_until_finished(job["id"], timeout=20)


def test_a_reread_waits_for_the_decision_and_changes_nothing_on_disk(api_client, configured_archive, fake_models):
    scan = configured_archive / "marked" / "08102025142000_202.png"
    before = (scan.read_bytes(), (scan.with_suffix(".json")).read_bytes())

    done = _reread(api_client)

    assert (done["status"], done["done"], done["failed"]) == ("succeeded", 2, 0)
    assert (scan.read_bytes(), scan.with_suffix(".json").read_bytes()) == before
    assert not list((configured_archive / "marked").glob("*.enhanced.png"))   # the temp scan is cleaned up
    assert models.loaded is None                                              # the model is unloaded after
    # the page now shows what the reread found
    body = api_client.get("/api/curate/workshop").json()
    assert body["reread"] == {"top_points": "right", "ocr_model": "Fake OCR", "extractor": "Fake LLM"}
    assert body["document"]["defaults"]["name"] == "ローソン 池袋店 (rescued)"
    assert body["document"]["ocr_text"].startswith("--- Page 1 ---\nre-read")
    assert body["document"]["pages"][0]["boxes"][0]["text"] == "合計"       # every box, since nothing cites one


def test_discarding_a_reread_goes_back_to_the_stored_reading(api_client, configured_archive, fake_models):
    _reread(api_client)
    body = api_client.delete("/api/curate/workshop/reread", params={"key": "9:202"}).json()
    assert body["reread"] is None and body["document"]["defaults"]["name"] == "ローソン 池袋店"


def test_accepting_keeps_the_reread_and_turns_the_page_it_read(api_client, configured_archive, fake_models):
    with Image.open(configured_archive / "marked" / "08102025142000_202.png") as before:
        width, height = before.size
    _reread(api_client)
    draft = {"document_type": "receipt", "name": "Rescued Shop", "date": "2025-08-10", "time": "14:20",
             "cost": 300.0, "currency": "JPY"}

    api_client.post("/api/curate/workshop/decide", json={"key": "9:202", "verdict": "accepted", "draft": draft})

    [record] = [r for r in api_client.get("/api/viz/records").json() if r["name"] == "Rescued Shop"]
    sidecar = read_sidecar(configured_archive / record["path"])
    assert sidecar.ocr.markdown.startswith("re-read") and sidecar.extraction.name == "ローソン 池袋店 (rescued)"
    with Image.open(configured_archive / record["path"]) as after:
        assert after.size == (height, width)


def test_a_reread_cannot_be_cancelled(api_client, fake_models):
    job = runner.start("workshop", "Reprocess", lambda progress: None, cancellable=False)
    assert api_client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_accepting_files_every_page_and_empties_the_queue(api_client, configured_archive, fake_models):
    _two_page_marked(configured_archive)
    draft = {"document_type": "receipt", "name": "Rescued Shop", "date": "2025-08-10", "time": "14:20",
             "cost": 300.0, "currency": "JPY"}

    body = api_client.post("/api/curate/workshop/decide",
                           json={"key": "9:202-203", "verdict": "accepted", "draft": draft,
                                 "comment": "fixed by hand"}).json()

    assert body["documents"] == [] and body["document"] is None
    assert not list((configured_archive / "marked").glob("*.png"))
    archived = [r for r in api_client.get("/api/viz/records").json() if r["name"] == "Rescued Shop"]
    assert len(archived) == 1 and len(archived[0]["paths"]) == 2          # one document, both pages
    assert archived[0]["comment"] == "fixed by hand"


def test_accepting_refuses_values_that_cannot_be_archived(api_client, configured_archive, fake_models):
    draft = {"document_type": "receipt", "name": "No Cost", "date": "2025-08-10", "time": "14:20",
             "cost": None, "currency": "JPY"}
    refused = api_client.post("/api/curate/workshop/decide",
                              json={"key": "9:202", "verdict": "accepted", "draft": draft})

    assert refused.status_code == 422
    assert (configured_archive / "marked" / "08102025142000_202.png").exists()   # still marked


def test_tossing_moves_the_document_out_of_marked(api_client, configured_archive, fake_models):
    draft = {"document_type": "receipt", "name": "x", "date": "", "time": "", "cost": 0.0, "currency": ""}

    body = api_client.post("/api/curate/workshop/decide",
                           json={"key": "9:202", "verdict": "tossed", "draft": draft}).json()

    assert body["documents"] == []
    assert (configured_archive / "tossed" / "08102025142000_202.png").exists()
    assert read_sidecar(configured_archive / "tossed" / "08102025142000_202.png").review.verdict == "tossed"


def test_the_workshop_only_accepts_or_tosses(api_client, configured_archive, fake_models):
    draft = {"document_type": "receipt", "name": "Sneaky", "date": "2025-08-10", "time": "14:20",
             "cost": 300.0, "currency": "JPY"}

    refused = api_client.post("/api/curate/workshop/decide",
                              json={"key": "9:202", "verdict": "marked", "draft": draft})

    assert refused.status_code == 422                       # marking again would strand the document
    assert (configured_archive / "marked" / "08102025142000_202.png").exists()


def test_a_document_that_has_gone_falls_back_instead_of_erroring(api_client, configured_archive, fake_models):
    body = api_client.get("/api/curate/workshop", params={"key": "9:999"}).json()

    assert body["document"]["key"] == "9:202"               # the page keeps working


def test_deciding_is_refused_while_a_workshop_job_runs(api_client, configured_archive, fake_models, monkeypatch):
    import threading

    gate = threading.Event()
    started = threading.Event()

    def slow_job(progress):
        started.set()
        assert gate.wait(10)
        return "done"

    runner.start("workshop", "Reprocess 9:202", slow_job)
    assert started.wait(10)
    draft = {"document_type": "receipt", "name": "x", "date": "", "time": "", "cost": 0.0, "currency": ""}

    refused = api_client.post("/api/curate/workshop/decide",
                              json={"key": "9:202", "verdict": "tossed", "draft": draft})

    assert refused.status_code == 409                       # the job is reading these very files
    gate.set()
    assert (configured_archive / "marked" / "08102025142000_202.png").exists()


def test_the_scan_preview_refuses_a_file_that_is_not_an_image(api_client, configured_archive):
    (configured_archive / "marked" / "notes.txt").write_text("not an image", encoding="utf-8")
    assert api_client.get("/api/curate/workshop/scan", params={"filename": "notes.txt"}).status_code == 415
    # a real file outside marked/ is still refused by the traversal guard
    assert api_client.get("/api/curate/workshop/scan",
                          params={"filename": "../brand_directory.json"}).status_code == 404


def test_the_payload_carries_every_page_so_the_page_can_show_them(api_client, configured_archive, fake_models):
    _two_page_marked(configured_archive)

    pages = api_client.get("/api/curate/workshop").json()["document"]["pages"]

    assert [p["filename"] for p in pages] == ["08102025142000_202.png", "08102025142100_203.png"]
    assert all(p["image_available"] for p in pages)
    for page in pages:                                   # each one renders through the treatment endpoint
        assert api_client.get("/api/curate/workshop/scan",
                              params={"filename": page["filename"], "treatment": "clahe"}).status_code == 200


def test_context_shows_the_week_around_the_form_and_the_batch(api_client, configured_archive):
    receipt = next(r for r in api_client.get("/api/viz/records").json() if r["document_type"] == "receipt")
    index = json.loads((configured_archive / "batches.json").read_text(encoding="utf-8"))
    index["batches"].append({"batch_id": 9, "start_datetime": "2025-08-10 14:00:00",
                             "end_datetime": "2025-08-10 15:00:00", "archived": True,
                             "files": {"201": "08102025143000_201.png", "202": "08102025142000_202.png",
                                       "203": receipt["filename"], "204": "never-archived.png"}})
    (configured_archive / "batches.json").write_text(json.dumps(index), encoding="utf-8")

    # the week follows the form's date and time, not the stored review
    body = api_client.get("/api/curate/workshop/context",
                          params={"key": "9:202", "date": receipt["date"], "time": receipt["time"]}).json()

    week = body["week"]
    assert [s["current"] for s in week].count(True) == 1
    archived = next(s for s in week if s["receipt"] == receipt["filename"])
    assert archived["verdict"] == "accepted" and archived["image"].startswith("/api/media/archived/")
    assert [s["date"] for s in week] == sorted(s["date"] for s in week)          # in time order

    assert body["batch_id"] == 9
    assert [(s["filename"], s["verdict"], s["current"]) for s in body["batch"]] == [
        ("08102025143000_201.png", "tossed", False),
        ("08102025142000_202.png", "marked", True),
        (receipt["filename"], "accepted", False),
        ("never-archived.png", "", False),
    ]
    assert body["batch"][0]["image"] == "/api/media/archived/tossed/08102025143000_201.png"


def _context(client, **params):
    response = client.get("/api/curate/workshop/context", params={"key": "9:202", **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_context_has_no_week_without_a_receipt_date(api_client, configured_archive):
    assert _context(api_client, date="", time="")["week"] is None
    assert _context(api_client, date="", time="14:20")["week"] is None
    assert _context(api_client, date="2025-08-10", time="14:20", document_type="other")["week"] is None
    assert _context(api_client, date="2025-02-31", time="10:00")["week"] is None     # shaped like a date, isn't one
    assert _context(api_client, date="", time="")["batch"] == []                   # batch 9 isn't in the index


def test_a_date_without_a_time_still_has_a_week(api_client, configured_archive):
    week = _context(api_client, date="2025-08-10", time="")["week"]
    assert week is not None and [s["current"] for s in week] == [True]
    assert _context(api_client, date="2025-08-10", time="25:00")["week"] is not None   # the date still counts


def test_one_impossible_date_in_the_archive_does_not_break_the_week(api_client, configured_archive):
    receipt = next(r for r in api_client.get("/api/viz/records").json() if r["document_type"] == "receipt")
    sidecar_path = (configured_archive / receipt["path"]).with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["review"]["date"] = "2025-02-31"
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    from api import cache
    cache.clear()

    assert _context(api_client, date=receipt["date"], time=receipt["time"])["week"] is not None


def test_a_marked_document_can_be_decided_while_parse_runs(api_client, configured_archive, fake_models):
    import threading

    from api.jobs import Claim

    release = threading.Event()
    runner.start("parse", "Parse (test)", lambda progress: release.wait(10), Claim(batches=frozenset({3})))
    draft = {"document_type": "receipt", "name": "x", "date": "", "time": "", "cost": 0.0, "currency": ""}
    try:
        response = api_client.post("/api/curate/workshop/decide", json={"key": "9:202", "verdict": "tossed", "draft": draft})
    finally:
        release.set()
    assert response.status_code == 200
