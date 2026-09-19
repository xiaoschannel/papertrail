"""Contract tests: File Index, OCR, Parse and Archive endpoints and background jobs, with fake models."""

import io
import json
import threading

import pytest
from PIL import Image

from api import ingest_registry
from api.jobs import EVERYTHING, FINISHED, Claim, JobConflict, JobRunner, runner
from api.model_manager import ModelManager, models
from data import load_decisions, load_extractions, load_ocr_results, save_decisions
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
    for job in runner.running_jobs():
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
    """A hosted extractor ("Fake LLM": loads nothing locally) and a local one ("Ollama - Fake": the GPU)."""
    unloads = []

    def extract(text, has_boxes=False, custom_instruction=""):
        return _receipt("Parsed")

    monkeypatch.setattr(ingest_registry, "extractors", lambda: {"Fake LLM": extract, "Ollama - Fake": extract})
    monkeypatch.setattr(ingest_registry, "unload_extractor", lambda name: lambda: unloads.append(name))
    return unloads


def _add_batch_2(client, scans):
    """Scan a second batch through File Index: one page, 2:1, with no OCR yet."""
    Image.new("RGB", (4, 4)).save(scans / "01112025090100_1.png")
    status = client.get("/api/ingest/index").json()
    assert [b["batch_id"] for b in status["proposal"]] == [2]
    return client.post("/api/ingest/index", json={"scheme": status["scheme"], "token": status["token"]})


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
    assert (status["extractors"], status["to_process"], status["tossed"]) == (["Fake LLM", "Ollama - Fake"], 5, 2)
    assert status["local_extractors"] == ["Ollama - Fake"] and status["waiting"] == 0

    job = ingest_client.post("/api/ingest/parse", json={"extractor": "Ollama - Fake", "reprocess": True, "limit": 2,
                                                         "custom_instruction": "Prefer Japanese names"}).json()
    assert (job["batches"], job["gpu"]) == ([1], True)   # a local model holds the GPU
    done = _finish(job)
    assert (done["status"], done["done"], done["failed"]) == ("succeeded", 2, 0)
    assert sum(getattr(e, "name", None) == "Parsed" for e in load_extractions(configured_ingest).values()) == 2
    assert fake_extractor == ["Ollama - Fake"] and models.loaded is None      # and unloads it when done
    assert ingest_client.get("/api/config").json()["parse_custom_instruction"] == "Prefer Japanese names"
    assert ingest_client.post("/api/ingest/parse", json={"extractor": "Nope"}).status_code == 422


def test_ocr_on_one_batch_while_parse_and_edits_run_on_another(ingest_client, configured_ingest, fake_ocr,
                                                                  fake_extractor, tmp_path):
    """The point of per-batch locks: OCR takes ages, so the rest of the pipeline keeps moving meanwhile."""
    Image.new("RGB", (4, 4)).save(tmp_path / "scans" / "01112025090100_1.png")   # batch 2's scan, not indexed yet
    fake_ocr.gate = threading.Event()
    ocr = ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "batch_id": 1, "reprocess": True,
                                                      "limit": 1}).json()
    assert fake_ocr.started.wait(10)
    assert (ocr["batches"], ocr["gpu"]) == ([1], True)

    # A new batch belongs to nobody yet: File Index can add it while OCR reads batch 1.
    status = ingest_client.get("/api/ingest/index").json()
    added = ingest_client.post("/api/ingest/index", json={"scheme": status["scheme"], "token": status["token"]})
    assert added.status_code == 200 and [b.batch_id for b in load_scan_index(configured_ingest).batches] == [1, 2]

    # Batch 2's pages can be rotated and regrouped; batch 1's can't, and the refusal says who holds it.
    assert ingest_client.post("/api/ingest/pages/rotate", json={"key": "2:1", "top_points": "left"}).status_code == 200
    assert ingest_client.put("/api/ingest/grouping", json={"batch_id": 2, "groups": []}).status_code == 200
    refused = ingest_client.post("/api/ingest/pages/rotate", json={"key": "1:1", "top_points": "left"})
    assert refused.status_code == 409 and refused.json()["detail"] == \
        "Can't rotate scans while OCR with Fake OCR is using batch 1."
    assert ingest_client.put("/api/ingest/grouping", json={"batch_id": 1, "groups": []}).status_code == 409
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:3"}).status_code == 200  # only Archive blocks

    # One of a kind, and Archive waits for everything - before it even plans.
    assert ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR"}).json()["detail"] == \
        "OCR with Fake OCR is still running."
    assert ingest_client.post("/api/ingest/archive").json()["detail"] == \
        "Archive has to wait: OCR with Fake OCR is running."

    # Parse plans around the batch OCR holds - here that leaves nothing, and it says why.
    assert [j["kind"] for j in ingest_client.get("/api/jobs").json() if j["status"] == "running"] == ["ocr"]
    hosted = ingest_client.post("/api/ingest/parse", json={"extractor": "Fake LLM", "reprocess": True})
    assert hosted.status_code == 409 and "another job is using" in hosted.json()["detail"]

    fake_ocr.gate.set()
    assert _finish(ocr)["status"] == "succeeded"


def test_hosted_parse_runs_beside_ocr_and_leaves_its_model_loaded(ingest_client, configured_ingest, fake_ocr,
                                                                   fake_extractor, tmp_path):
    """OCR reads the new batch 2 while Parse extracts batch 1 with a hosted model, at the same time."""
    assert _add_batch_2(ingest_client, tmp_path / "scans").status_code == 200
    fake_ocr.gate = threading.Event()
    ocr = ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR"}).json()   # only 2:1 needs reading
    assert fake_ocr.started.wait(10)
    assert ocr["batches"] == [2]

    # Parse and OCR plan around what the other holds; the page counts say what is waiting.
    assert ingest_client.get("/api/ingest/ocr", params={"reprocess": True}).json()["waiting"] == 1
    parse = ingest_client.post("/api/ingest/parse", json={"extractor": "Fake LLM", "reprocess": True}).json()
    assert (parse["batches"], parse["gpu"]) == ([1], False)
    done = _finish(parse)
    assert (done["status"], done["done"]) == ("succeeded", 5)
    assert runner.get(ocr["id"])["status"] == "running"      # the whole Parse ran while OCR was still reading
    # The hosted model loaded nothing, so finishing it didn't unload the OCR model still in use.
    assert models.loaded == "ocr:Fake OCR" and fake_ocr.teardowns == 0 and fake_extractor == []

    # A local model would need the GPU OCR holds.
    local = ingest_client.post("/api/ingest/parse", json={"extractor": "Ollama - Fake", "reprocess": True})
    assert local.status_code == 409 and local.json()["detail"] == "Parse with Ollama - Fake has to wait: OCR with Fake OCR is using the GPU."

    fake_ocr.gate.set()
    assert _finish(ocr)["status"] == "succeeded"
    assert "2:1" in load_ocr_results(configured_ingest) and fake_ocr.teardowns == 1


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

    decisions = load_decisions(configured_ingest)
    del decisions["1:3"]
    save_decisions(configured_ingest, decisions)
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
    decision = {"key": "1:3", "verdict": "tossed", "draft": {"document_type": "receipt", "name": "x", "date": "",
                                                            "time": "", "cost": None, "currency": ""}}
    assert ingest_client.post("/api/review/undo", json=decision).status_code == 409
    assert ingest_client.post("/api/review/decisions", json=decision).status_code == 409
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:3"}).status_code == 409

    gate.set()
    done = _finish(job)
    # conftest only provides page 1:1's scan, so the other seven copies fail and nothing is finalized
    assert (done["status"], done["done"], done["failed"]) == ("failed", 8, 7)
    assert ingest_client.delete("/api/review/decisions").status_code == 200


# --- runner and model manager units --------------------------------------------------------------------------
def test_claims_collide_on_a_shared_batch_the_gpu_or_everything():
    ocr_12 = Claim(batches=frozenset({12}), gpu=True)
    assert not ocr_12.collides_with(Claim(batches=frozenset({11})))           # hosted Parse on another batch
    assert ocr_12.collides_with(Claim(batches=frozenset({11}), gpu=True))     # local Parse: one GPU
    assert ocr_12.collides_with(Claim(batches=frozenset({12, 13})))           # same batch
    assert ocr_12.collides_with(EVERYTHING) and EVERYTHING.collides_with(Claim())
    assert not ocr_12.collides_with(Claim())                                   # adding a batch
    assert ocr_12.shared_with(Claim(gpu=True)) == "the GPU"
    assert Claim(batches=frozenset({3, 4, 5})).shared_with(Claim(batches=frozenset({3, 4}))) == "batches 3 and 4"


def test_runner_lists_running_jobs_and_the_last_of_each_kind():
    local, gates = JobRunner(), {kind: threading.Event() for kind in ("a", "b")}

    def waits_for(kind):
        return lambda progress: gates[kind].wait(10) and None

    first = local.start("a", "A", lambda progress: None, Claim(batches=frozenset({1})))
    local.wait_until_finished(first["id"], timeout=10)
    running = local.start("a", "A again", waits_for("a"), Claim(batches=frozenset({1})))
    other = local.start("b", "B", waits_for("b"), Claim(batches=frozenset({2})))
    assert [j["id"] for j in local.recent()] == [running["id"], other["id"]]      # the finished A is superseded
    assert local.held_batches() == frozenset({1, 2})
    with pytest.raises(JobConflict, match="A again is still running"):
        local.start("a", "A third", lambda progress: None, Claim(batches=frozenset({9})))  # one of a kind
    for gate in gates.values():
        gate.set()
    for job in (running, other):
        local.wait_until_finished(job["id"], timeout=10)
    assert local.held_batches() == frozenset() and local.running() is None


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
