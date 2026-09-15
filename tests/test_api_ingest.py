"""Contract tests: File Index, OCR, Parse and Archive endpoints and background jobs, with fake models."""

import io
import json
import threading

import pytest
from PIL import Image

from api import ingest_registry
from api.jobs import FINISHED, JobConflict, JobRunner, runner
from api.model_manager import ModelManager, models
from data import load_decisions, load_extractions, load_ocr_results
from models import ReceiptResult, load_scan_index

PAGE_1 = "01102025132642_1.png"  # the only scan conftest puts in the input folder (page 1:1)


class GatedOcr:
    """Fake OCR model; ``gate`` (if set) holds each page until the test releases it."""

    grounding = True

    def __init__(self, gate: threading.Event | None = None):
        self.gate = gate
        self.started = threading.Event()
        self.teardowns = 0

    def run(self, path, structured=False):
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(10)
        return "<|ref|>合計<|/ref|><|det|>[[1, 2, 3, 4]]<|/det|>" if structured else f"text of {path.name}"

    def teardown(self):
        self.teardowns += 1


def _receipt(name):
    return ReceiptResult(document_type="receipt", language="ja", date="2025-01-10", time="10:00", name=name,
                         currency="JPY", address="", cost=100.0)


@pytest.fixture(autouse=True)
def no_leftover_job():
    yield
    job = runner.current()
    if job and job["status"] not in FINISHED:
        runner.cancel(job["id"])
        runner.wait_until_finished(job["id"], timeout=10)
    models.release()


@pytest.fixture
def fake_ocr(monkeypatch):
    provider = GatedOcr()
    monkeypatch.setattr(ingest_registry, "ocr_providers", lambda: {"Fake OCR": provider})
    return provider


@pytest.fixture
def fake_extractor(monkeypatch):
    unloads = []
    monkeypatch.setattr(ingest_registry, "extractors",
                        lambda: {"Fake LLM": lambda text, has_boxes=False, custom_instruction="": _receipt("Parsed")})
    monkeypatch.setattr(ingest_registry, "unload_extractor", lambda name: lambda: unloads.append(name))
    return unloads


def _finish(job):
    done = runner.wait_until_finished(job["id"], timeout=10)
    assert done["status"] in FINISHED, done
    return done


# --- File Index ------------------------------------------------------------------------------------------
def test_index_propose_confirm_and_stale_token(ingest_client, configured_ingest, tmp_path):
    for serial in (1, 2):
        Image.new("RGB", (4, 4)).save(tmp_path / "scans" / f"01112025090{serial}00_{serial}.png")

    status = ingest_client.get("/api/ingest/index").json()
    assert status["blocker"] is None and status["scheme"] == "Canon ImageFormula"
    [batch] = status["proposal"]
    assert batch["batch_id"] == 2 and [f["serial"] for f in batch["files"]] == [1, 2]

    stale = ingest_client.post("/api/ingest/index", json={"scheme": status["scheme"], "token": "outdated"})
    assert stale.status_code == 409

    confirmed = ingest_client.post("/api/ingest/index", json={"scheme": status["scheme"], "token": status["token"]})
    assert confirmed.status_code == 200 and confirmed.json()["proposal"] == []
    assert [b.batch_id for b in load_scan_index(configured_ingest).batches] == [1, 2]


def test_index_rejects_unknown_scheme_and_reports_missing_input(ingest_client, tmp_path):
    assert ingest_client.get("/api/ingest/index", params={"scheme": "Nope"}).status_code == 422
    for f in (tmp_path / "scans").iterdir():
        f.unlink()
    (tmp_path / "scans").rmdir()
    assert ingest_client.get("/api/ingest/index").json()["blocker"].startswith("The input image folder doesn't exist")


# --- Grouping ---------------------------------------------------------------------------------------------
def test_grouping_state_and_save(ingest_client, configured_ingest):
    body = ingest_client.get("/api/ingest/grouping").json()
    assert body["batch_id"] == 1 and body["saved_groups"] == [["1:4", "1:5"]]
    pages = {p["key"]: p for p in body["pages"]}
    assert pages["1:1"]["image_available"] and not pages["1:2"]["image_available"]
    assert pages["1:6"]["tossed"]

    # 1:6 is tossed (the save below clears that decision, so check first)
    assert ingest_client.put("/api/ingest/grouping", json={"batch_id": 1, "groups": [["1:6", "1:7"]]}).status_code == 422
    unchanged = [["1:1"], ["1:2"], ["1:3"], ["1:4", "1:5"], ["1:7"]]
    assert ingest_client.put("/api/ingest/grouping", json={"batch_id": 1, "groups": unchanged}).json() == {"changed": False}
    changed = ingest_client.put("/api/ingest/grouping", json={"batch_id": 1, "groups": [["1:1", "1:2"]]})
    assert changed.json() == {"changed": True} and load_extractions(configured_ingest) == {}

    assert ingest_client.put("/api/ingest/grouping", json={"batch_id": 9, "groups": []}).status_code == 404
    # a batch that is gone (e.g. archived while the page was open) falls back to the first unarchived one
    assert ingest_client.get("/api/ingest/grouping", params={"batch_id": 9}).json()["batch_id"] == 1


def test_toss_recover_and_rotate(ingest_client, configured_ingest, tmp_path):
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:5"}).status_code == 200
    assert load_decisions(configured_ingest)["1:4-5"].verdict == "tossed"
    assert ingest_client.post("/api/ingest/pages/recover", json={"key": "1:5"}).status_code == 200
    assert "1:4-5" not in load_decisions(configured_ingest)
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:99"}).status_code == 404

    before = next(p for p in ingest_client.get("/api/ingest/grouping").json()["pages"] if p["key"] == "1:1")
    with Image.open(tmp_path / "scans" / PAGE_1) as img:
        width, height = img.size
    assert ingest_client.post("/api/ingest/pages/rotate", json={"key": "1:1", "top_points": "left"}).status_code == 200
    with Image.open(tmp_path / "scans" / PAGE_1) as img:
        assert img.size == (height, width)
    after = next(p for p in ingest_client.get("/api/ingest/grouping").json()["pages"] if p["key"] == "1:1")
    assert after["image_version"] != before["image_version"]
    assert ingest_client.post("/api/ingest/pages/rotate", json={"key": "1:2", "top_points": "down"}).status_code == 404


def test_input_thumbnail(ingest_client, tmp_path):
    Image.new("RGB", (400, 800), "white").save(tmp_path / "scans" / PAGE_1)
    response = ingest_client.get(f"/api/media/input-thumb/{PAGE_1}", params={"width": 100})
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.content)).width == 100
    assert ingest_client.get(f"/api/media/input-thumb/{PAGE_1}", params={"width": 10}).status_code == 422
    assert ingest_client.get("/api/media/input-thumb/missing.png").status_code == 404


# --- OCR / Parse jobs ----------------------------------------------------------------------------------------
def test_ocr_job_saves_results_and_unloads_the_model(ingest_client, configured_ingest, fake_ocr):
    status = ingest_client.get("/api/ingest/ocr", params={"reprocess": True}).json()
    assert status["providers"] == ["Fake OCR"] and status["grounding"] and status["to_process"] == 1

    job = ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "reprocess": True}).json()
    assert job["kind"] == "ocr" and job["status"] == "running"
    done = _finish(job)
    assert (done["status"], done["total"], done["done"], done["failed"]) == ("succeeded", 1, 1, 0)

    result = load_ocr_results(configured_ingest)["1:1"]
    assert result.markdown == f"text of {PAGE_1}" and result.boxes[0].coords == [[1, 2, 3, 4]]
    assert fake_ocr.teardowns == 1 and models.loaded is None
    assert ingest_client.get("/api/config").json()["ocr_model"] == "Fake OCR"


def test_ocr_refuses_unknown_model_and_empty_plans(ingest_client, fake_ocr):
    assert ingest_client.post("/api/ingest/ocr", json={"provider": "Nope"}).status_code == 422
    assert ingest_client.get("/api/ingest/ocr", params={"provider": "Nope"}).status_code == 422
    assert ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "batch_id": 2}).status_code == 422


def test_parse_job(ingest_client, configured_ingest, fake_extractor):
    status = ingest_client.get("/api/ingest/parse", params={"reprocess": True}).json()
    assert (status["extractors"], status["to_process"], status["tossed"]) == (["Fake LLM"], 5, 2)

    job = ingest_client.post("/api/ingest/parse", json={"extractor": "Fake LLM", "reprocess": True, "limit": 2,
                                                         "custom_instruction": "Prefer Japanese names"}).json()
    done = _finish(job)
    assert (done["status"], done["done"], done["failed"]) == ("succeeded", 2, 0)
    assert sum(getattr(e, "name", None) == "Parsed" for e in load_extractions(configured_ingest).values()) == 2
    assert fake_extractor == ["Fake LLM"]
    assert ingest_client.get("/api/config").json()["parse_custom_instruction"] == "Prefer Japanese names"
    assert ingest_client.post("/api/ingest/parse", json={"extractor": "Nope"}).status_code == 422


def test_one_job_at_a_time_and_edits_refused_while_it_runs(ingest_client, fake_ocr, fake_extractor):
    fake_ocr.gate = threading.Event()
    job = ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "reprocess": True}).json()
    assert fake_ocr.started.wait(10)

    assert ingest_client.get("/api/jobs/current").json()["id"] == job["id"]
    assert ingest_client.post("/api/ingest/parse", json={"extractor": "Fake LLM", "reprocess": True}).status_code == 409
    assert ingest_client.put("/api/ingest/grouping", json={"batch_id": 1, "groups": []}).status_code == 409
    assert ingest_client.post("/api/ingest/pages/rotate", json={"key": "1:1", "top_points": "left"}).status_code == 409
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:3"}).status_code == 200  # only Archive blocks

    fake_ocr.gate.set()
    assert _finish(job)["status"] == "succeeded"


def test_cancel_and_event_stream(ingest_client, fake_ocr):
    fake_ocr.gate = threading.Event()
    job = ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "reprocess": True}).json()
    assert fake_ocr.started.wait(10)

    cancelled = ingest_client.post(f"/api/jobs/{job['id']}/cancel").json()
    assert cancelled["cancel_requested"] and cancelled["status"] == "running"
    fake_ocr.gate.set()
    assert _finish(job)["status"] == "cancelled"

    with ingest_client.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[len("data: "):]) for line in response.iter_lines() if line.startswith("data: ")]
    assert events[-1]["status"] == "cancelled" and events[-1]["id"] == job["id"]

    assert ingest_client.get("/api/jobs/nope").status_code == 404
    assert ingest_client.post("/api/jobs/nope/cancel").status_code == 404
    assert ingest_client.get("/api/jobs/nope/events").status_code == 404


# --- Archive -------------------------------------------------------------------------------------------------
def test_archive_status_and_blocker(ingest_client, configured_ingest):
    status = ingest_client.get("/api/ingest/archive").json()
    assert status["blocker"] is None and (status["files"], status["tossed"]) == (8, 2)
    assert {m["key"]: m["destination"] for m in status["moves"]}["1:6"] == "tossed/01102025142000_6.png"

    ingest_client.delete("/api/review/decision", params={"key": "1:3"})
    blocked = ingest_client.post("/api/ingest/archive")
    assert blocked.status_code == 422 and blocked.json()["detail"] == "Review all files before archiving."


def test_archive_job_reports_missing_scans_and_blocks_review_edits(ingest_client, monkeypatch):
    import api.routers.ingest as ingest_router

    gate, started = threading.Event(), threading.Event()
    real_run_archive = ingest_router.pipeline.run_archive

    def gated(output_path, input_path, progress):
        started.set()
        assert gate.wait(10)
        return real_run_archive(output_path, input_path, progress)

    monkeypatch.setattr(ingest_router.pipeline, "run_archive", gated)
    job = ingest_client.post("/api/ingest/archive").json()
    assert started.wait(10)
    assert ingest_client.delete("/api/review/decisions").status_code == 409
    assert ingest_client.delete("/api/review/decision", params={"key": "1:3"}).status_code == 409
    decision = {"key": "1:3", "verdict": "tossed", "draft": {"document_type": "receipt", "name": "x", "date": "",
                                                            "time": "", "cost": None, "currency": ""}}
    assert ingest_client.post("/api/review/decisions", json=decision).status_code == 409
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:3"}).status_code == 409

    gate.set()
    done = _finish(job)
    # conftest only provides page 1:1's scan, so the other seven copies fail and nothing is finalized
    assert (done["status"], done["done"], done["failed"]) == ("failed", 8, 7)
    assert ingest_client.delete("/api/review/decisions").status_code == 200


# --- runner and model manager units --------------------------------------------------------------------------
def test_job_runner_statuses_and_conflict():
    local = JobRunner()
    gate = threading.Event()

    def slow(progress):
        progress.set_total(2)
        progress.tick(item="a")
        assert gate.wait(10)
        progress.tick(ok=False, item="b", error="bad")
        return "finished"

    job = local.start("test", "Slow", slow)
    with pytest.raises(JobConflict):
        local.start("test", "Another", lambda progress: None)
    gate.set()
    done = local.wait_until_finished(job["id"], timeout=10)
    assert (done["status"], done["done"], done["failed"], done["message"]) == ("succeeded", 2, 1, "finished")
    assert done["errors"] == [{"item": "b", "error": "bad"}]

    def boom(progress):
        raise RuntimeError("kaput")

    crashed = local.wait_until_finished(local.start("test", "Boom", boom)["id"], timeout=10)
    assert (crashed["status"], crashed["message"]) == ("failed", "RuntimeError: kaput")
    assert local.running() is None


def test_job_that_crashes_or_is_cancelled_still_unloads_its_model(ingest_client, fake_ocr, monkeypatch):
    import api.routers.ingest as ingest_router

    def crash(*args, **kwargs):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(ingest_router.pipeline, "run_ocr", crash)
    done = _finish(ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "reprocess": True}).json())
    assert (done["status"], done["message"]) == ("failed", "RuntimeError: CUDA out of memory")
    assert fake_ocr.teardowns == 1 and models.loaded is None


def test_model_manager_unloads_before_switching():
    manager, calls = ModelManager(), []
    manager.acquire("a", lambda: calls.append("a"))
    manager.acquire("a", lambda: calls.append("a again"))  # same model: nothing to unload
    manager.acquire("b", lambda: (_ for _ in ()).throw(RuntimeError("unload failed")))
    assert calls == ["a"] and manager.loaded == "b"
    manager.release()  # a failing unload is logged, not raised
    assert manager.loaded is None
