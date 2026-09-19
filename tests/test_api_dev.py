"""Contract tests: the Dev pages — Sanity Check, Index Audit and the Experiment bench."""

import io
import json

import pytest
from PIL import Image

import experiment_runs
from api import ingest_registry
from api.jobs import FINISHED, runner
from api.model_manager import models
from models import ReceiptResult
from settings import get_config


@pytest.fixture(autouse=True)
def no_leftover_job():
    yield
    for job in runner.running_jobs():
        runner.cancel(job["id"])
        runner.wait_until_finished(job["id"], timeout=10)
    models.release()


# --- Sanity Check ----------------------------------------------------------------------------------------
def test_sanity_reports_a_clean_archive(api_client):
    body = api_client.get("/api/dev/sanity").json()

    assert body["indexed"] is True
    assert body["sidecar_mismatches"] == []
    [batch] = body["batches"]
    assert batch == {"batch_id": 5, "archived": True, "files": 9, "organized": 9,
                     "missing_from_archive": [], "missing_from_input": None}


def test_sanity_names_a_scan_without_its_sidecar(api_client, configured_archive):
    (configured_archive / "2025/01/2025年1月15日 09：05 セブン-イレブン 上野店.json").unlink()

    body = api_client.get("/api/dev/sanity").json()

    assert body["sidecar_mismatches"] == [{"folder": "2025/01",
                                           "missing_sidecar": ["2025年1月15日 09：05 セブン-イレブン 上野店"],
                                           "extra_sidecar": []}]


def test_sanity_checks_a_batch_being_ingested_against_the_scan_folder(api_client, configured_archive, tmp_path):
    index = json.loads((configured_archive / "batches.json").read_text(encoding="utf-8"))
    index["batches"].append({"batch_id": 6, "start_datetime": "2026-01-01 10:00:00",
                             "end_datetime": "2026-01-01 10:01:00", "archived": False,
                             "files": {"1": "01012026100000_1.png", "2": "01012026100100_2.png"}})
    (configured_archive / "batches.json").write_text(json.dumps(index), encoding="utf-8")
    scans = tmp_path / "scans"
    scans.mkdir()
    Image.new("RGB", (4, 4)).save(scans / "01012026100000_1.png")

    ingesting = api_client.get("/api/dev/sanity").json()["batches"][1]

    assert ingesting["archived"] is False and ingesting["missing_from_archive"] == []
    assert ingesting["missing_from_input"] == ["01012026100100_2.png"]


def test_sanity_without_an_index_says_so(api_client, configured_archive):
    (configured_archive / "batches.json").unlink()
    body = api_client.get("/api/dev/sanity").json()
    assert body["indexed"] is False and body["batches"] == []


# --- Index Audit ---------------------------------------------------------------------------------------
def test_index_audit_totals_and_batches(api_client):
    body = api_client.get("/api/dev/index-audit").json()

    assert body["index_file"]["size"] > 0
    assert (body["total_batches"], body["archived"], body["non_archived"]) == (1, 1, 0)
    assert (body["total_entries"], body["unique_filenames"], body["lost_to_dedup"]) == (9, 9, 0)
    assert body["duplicates"] == []
    assert body["batches"] == [{"batch_id": 5, "files": 9, "running_total": 9, "archived": True,
                                "start": "2023-05-18 16:45:00", "end": "2025-03-02 10:00:00"}]
    assert body["input"] is None                      # the configured scan folder doesn't exist


def test_index_audit_finds_a_file_indexed_twice_and_the_scan_folder_delta(api_client, configured_archive, tmp_path):
    index = json.loads((configured_archive / "batches.json").read_text(encoding="utf-8"))
    index["batches"].append({"batch_id": 6, "start_datetime": "2026-01-01 10:00:00",
                             "end_datetime": "2026-01-01 10:01:00", "archived": False,
                             "files": {"1": "01102025132642_101.png"}})
    (configured_archive / "batches.json").write_text(json.dumps(index), encoding="utf-8")
    scans = tmp_path / "scans"
    scans.mkdir()
    for name in ("01102025132642_101.png", "09092026090000_1.png"):
        Image.new("RGB", (4, 4)).save(scans / name)
    (scans / "notes.txt").write_text("not a scan", encoding="utf-8")

    body = api_client.get("/api/dev/index-audit").json()

    assert body["duplicates"] == [{"filename": "01102025132642_101.png", "count": 2, "batch_ids": [5, 6]}]
    assert body["lost_to_dedup"] == 1
    assert body["batches"][1]["running_total"] == 10
    assert body["input"] == {"on_disk": 2, "indexed_not_on_disk": 8, "on_disk_not_indexed": ["09092026090000_1.png"]}


# --- Experiment ------------------------------------------------------------------------------------------
@pytest.fixture
def bench(monkeypatch, tmp_path, configured_archive):
    """Runs in a temp folder, with fake models: a grounding OCR and an extractor that cites box 0."""
    monkeypatch.setattr(experiment_runs, "ROOT", tmp_path / "experiment")
    seen = {"ocr": [], "prompts": []}

    class FakeOcr:
        grounding = True

        def run(self, path, structured=False):
            with Image.open(path) as image:
                seen["ocr"].append((path.name, image.size, structured))
            return "<|ref|>合計 ¥300<|/ref|><|det|>[[100, 200, 300, 400]]<|/det|>" if structured else "合計 ¥300"

        def teardown(self):
            pass

    class PlainOcr(FakeOcr):
        grounding = False

    def fake_extract(text, has_boxes=False, custom_instruction=""):
        seen["prompts"].append((text, has_boxes, custom_instruction))
        return ReceiptResult(document_type="receipt", language="ja", date="2026-01-01", time="10:00",
                             name="Example Shop", currency="JPY", address="", cost=300.0,
                             field_sources={"cost": ["1:0"]} if has_boxes else {})

    monkeypatch.setattr(ingest_registry, "ocr_providers", lambda: {"Fake OCR": FakeOcr(), "Plain OCR": PlainOcr()})
    monkeypatch.setattr(ingest_registry, "extractors", lambda: {"Fake LLM": fake_extract})
    monkeypatch.setattr(ingest_registry, "unload_extractor", lambda name: (lambda: None))
    return seen


def _png(size=(40, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(api_client, name="my receipt.png", data=None):
    response = api_client.post("/api/dev/experiment", files={"file": (name, data or _png(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()


def _finish(api_client, response):
    assert response.status_code == 200, response.text
    job = runner.wait_until_finished(response.json()["id"], timeout=10)
    assert job["status"] == "succeeded", job["message"]
    return job


def test_an_upload_is_kept_outside_the_archive_and_opens_next_time(api_client, bench, configured_archive, tmp_path):
    before = sorted(p.relative_to(configured_archive) for p in configured_archive.rglob("*"))

    run = _upload(api_client)

    assert (run["filename"], run["width"], run["height"]) == ("my receipt.png", 40, 80)
    assert run["ocr"] is None and run["parse"] is None
    assert (tmp_path / "experiment" / run["id"] / "my receipt.png").is_file()
    assert sorted(p.relative_to(configured_archive) for p in configured_archive.rglob("*")) == before
    options = api_client.get("/api/dev/experiment").json()
    assert options["latest"] == run["id"]
    assert options["ocr_models"] == ["Fake OCR", "Plain OCR"] and options["grounding_models"] == ["Fake OCR"]


def test_an_upload_that_isnt_an_image_is_refused(api_client, bench):
    assert api_client.post("/api/dev/experiment", files={"file": ("a.png", b"not a png", "image/png")}).status_code == 415
    assert api_client.post("/api/dev/experiment", files={"file": ("a.txt", _png(), "text/plain")}).status_code == 415


def test_only_the_newest_runs_are_kept(api_client, bench, monkeypatch):
    monkeypatch.setattr(experiment_runs, "KEEP", 2)
    first = _upload(api_client)["id"]
    _upload(api_client)
    _upload(api_client)
    assert api_client.get(f"/api/dev/experiment/{first}").status_code == 404


def test_the_preview_is_the_treated_image(api_client, bench):
    run = _upload(api_client)
    response = api_client.get(f"/api/dev/experiment/{run['id']}/scan",
                              params={"top_points": "left", "treatment": "clahe", "denoise_after": "true"})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(response.content)).size == (80, 40)


def test_ocr_reads_the_treated_image_and_keeps_the_text_the_boxes_and_what_it_read(api_client, bench):
    run = _upload(api_client)

    job = _finish(api_client, api_client.post(f"/api/dev/experiment/{run['id']}/ocr",
                                              json={"model": "Fake OCR", "top_points": "left"}))

    assert job["done"] == 2
    assert bench["ocr"] == [("my receipt.enhanced.png", (80, 40), False), ("my receipt.enhanced.png", (80, 40), True)]
    ocr = api_client.get(f"/api/dev/experiment/{run['id']}").json()["ocr"]
    assert ocr["model"] == "Fake OCR" and ocr["with_boxes"] is True
    assert ocr["markdown"] == "合計 ¥300" and "<|det|>" in ocr["structured_raw"]
    assert ocr["boxes"] == [{"index": 0, "fields": [], "text": "合計 ¥300",
                             "rects": [{"x1": 100, "y1": 200, "x2": 300, "y2": 400}]}]
    assert ocr["treatment"]["top_points"] == "left"
    seen = api_client.get(f"/api/dev/experiment/{run['id']}/seen")
    assert Image.open(io.BytesIO(seen.content)).size == (80, 40)
    assert get_config().ocr_model == "Fake OCR"           # the model tried becomes the pipeline's, as in Streamlit


def test_a_model_without_boxes_reads_once(api_client, bench):
    run = _upload(api_client)
    _finish(api_client, api_client.post(f"/api/dev/experiment/{run['id']}/ocr", json={"model": "Plain OCR"}))
    ocr = api_client.get(f"/api/dev/experiment/{run['id']}").json()["ocr"]
    assert [structured for *_, structured in bench["ocr"]] == [False]
    assert ocr["with_boxes"] is False and ocr["boxes"] == [] and ocr["structured_raw"] is None


def test_parse_sends_the_pipelines_input_and_the_preview_shows_its_prompt(api_client, bench):
    run = _upload(api_client)
    assert api_client.post(f"/api/dev/experiment/{run['id']}/parse", json={"extractor": "Fake LLM"}).status_code == 409
    _finish(api_client, api_client.post(f"/api/dev/experiment/{run['id']}/ocr", json={"model": "Fake OCR"}))

    preview = api_client.post(f"/api/dev/experiment/{run['id']}/prompt",
                              json={"custom_instruction": "Prefer the Japanese name"}).json()
    _finish(api_client, api_client.post(f"/api/dev/experiment/{run['id']}/parse",
                                        json={"extractor": "Fake LLM", "custom_instruction": "Prefer the Japanese name"}))

    assert bench["prompts"] == [("--- Page 1 ---\n合計 ¥300\n--- Page 1 Grounding Boxes ---\n[P1-BOX-0] 合計 ¥300",
                                 True, "Prefer the Japanese name")]
    assert preview["has_boxes"] is True
    assert "Prefer the Japanese name" in preview["prompt"] and "[P1-BOX-0] 合計 ¥300" in preview["prompt"]
    parse = api_client.get(f"/api/dev/experiment/{run['id']}").json()["parse"]
    assert parse["extraction"]["name"] == "Example Shop"
    assert [(b["index"], b["fields"]) for b in parse["field_boxes"]] == [(0, ["cost"])]
    assert get_config().parse_custom_instruction == "Prefer the Japanese name"


def test_reading_again_drops_the_extraction_of_the_old_text(api_client, bench):
    run = _upload(api_client)
    _finish(api_client, api_client.post(f"/api/dev/experiment/{run['id']}/ocr", json={"model": "Fake OCR"}))
    _finish(api_client, api_client.post(f"/api/dev/experiment/{run['id']}/parse", json={"extractor": "Fake LLM"}))
    _finish(api_client, api_client.post(f"/api/dev/experiment/{run['id']}/ocr", json={"model": "Plain OCR"}))
    assert api_client.get(f"/api/dev/experiment/{run['id']}").json()["parse"] is None


def test_an_unknown_run_or_model_is_refused(api_client, bench):
    assert api_client.get("/api/dev/experiment/" + "0" * 32).status_code == 404
    assert api_client.get("/api/dev/experiment/../../etc").status_code == 404
    run = _upload(api_client)
    assert api_client.post(f"/api/dev/experiment/{run['id']}/ocr", json={"model": "Nope"}).status_code == 422
