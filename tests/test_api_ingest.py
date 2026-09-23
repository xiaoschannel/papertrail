"""Contract tests: File Index, OCR, Parse and Archive endpoints and background jobs, with fake models."""

import io
import json
import threading
from pathlib import Path

import pytest
from PIL import Image

import deskew
import orientation
from tilt_scans import scan as tilt_fixture
from api import ingest_registry
from api.jobs import EVERYTHING, FINISHED, Claim, JobConflict, JobRunner, runner
from api.model_manager import ModelManager, models
from data import load_decisions, load_extractions, load_ocr_results, load_rotation_log, save_decisions
from models import ReceiptResult, TokenUse, load_scan_index

PAGE_1 = "01102025132642_1.png"  # the only scan conftest puts in the input folder (page 1:1)
#: Printed pages for the orientation checks, and how to make one face each way (tests/fixtures/orientation).
PRINTED = Path(__file__).resolve().parent / "fixtures" / "orientation"
FACING = {"left": Image.Transpose.ROTATE_90, "right": Image.Transpose.ROTATE_270, "down": Image.Transpose.ROTATE_180}


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


def decide(client, key, filename, fix=True, top=None, degrees=0.0, source="queue"):
    """Decide on a scan at the version it is now (Fix Rotation's queue does, from what it showed)."""
    version = (Path(client.get("/api/config").json()["root_path"]) / "scans" / filename).stat().st_mtime_ns
    return client.post("/api/ingest/rotation/decide", json={"key": key, "image_version": version, "fix": fix,
                                                             "top_points": top, "degrees": degrees, "source": source})


def rotation_queue(client):
    return client.get("/api/ingest/rotation").json()


def _finish(job):
    done = runner.wait_until_finished(job["id"], timeout=10)
    assert done["status"] in FINISHED, done
    return done


# --- File Index ------------------------------------------------------------------------------------------
def printed(name: str = "receipt.png", top_points: str | None = None) -> Image.Image:
    """A printed fixture page, turned so its top points ``top_points`` (upright when None)."""
    with Image.open(PRINTED / name) as img:
        return img.transpose(FACING[top_points]) if top_points else img.copy()


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
def _finalize_group(client, *groupings):
    return client.post("/api/ingest/finalize/group",
                       json={"groups": [{"batch_id": b, "groups": g} for b, g in groupings]})


def test_grouping_state_and_save(ingest_client, configured_ingest):
    body = ingest_client.get("/api/ingest/grouping").json()
    assert body["batch_id"] == 1 and body["saved_groups"] == [["1:4", "1:5"]]
    pages = {p["key"]: p for p in body["pages"]}
    assert pages["1:1"]["image_available"] and not pages["1:2"]["image_available"]
    assert pages["1:6"]["tossed"]

    # 1:6 is tossed (the save below clears that decision, so check first)
    assert _finalize_group(ingest_client, (1, [["1:6", "1:7"]])).status_code == 422
    unchanged = [["1:1"], ["1:2"], ["1:3"], ["1:4", "1:5"], ["1:7"]]
    assert _finalize_group(ingest_client, (1, unchanged)).status_code == 200
    assert "1:1" in load_extractions(configured_ingest)                      # unchanged: nothing cleared
    assert _finalize_group(ingest_client, (1, [["1:1", "1:2"]])).status_code == 200
    assert load_extractions(configured_ingest) == {}

    assert _finalize_group(ingest_client, (9, [])).status_code == 404
    # a batch that is gone (e.g. archived while the page was open) falls back to the first unarchived one
    assert ingest_client.get("/api/ingest/grouping", params={"batch_id": 9}).json()["batch_id"] == 1


def test_finalize_commits_each_step_s_files_for_every_batch_and_nothing_else(ingest_client, configured_ingest, tmp_path):
    import archive_history

    root = configured_ingest.parent
    status = ingest_client.get("/api/ingest/finalize/rotation").json()
    assert [b["batch_id"] for b in status["batches"]] == [1] and status["changed"] is None     # no history yet
    _add_batch_2(ingest_client, tmp_path / "scans")                      # File Index's commit starts the history
    status = ingest_client.get("/api/ingest/finalize/rotation").json()
    assert [b["batch_id"] for b in status["batches"]] == [1, 2]
    assert status["changed"] > 0                   # batch 1's scan and working files predate the history

    printed(top_points="left").save(tmp_path / "scans" / PAGE_1)
    assert decide(ingest_client, "1:1", PAGE_1, top="left").status_code == 200
    (tmp_path / "scans" / "dropped in, not indexed.png").write_bytes(b"")
    draft = {"document_type": "receipt", "name": "Shop", "date": "2025-01-10", "time": "10:00", "cost": 100,
             "currency": "JPY"}
    assert ingest_client.post("/api/review/decisions", json={"key": "1:3", "verdict": "accepted", "draft": draft}).status_code == 200

    done = ingest_client.post("/api/ingest/finalize/rotation", json={}).json()
    assert done["committed"] and done["changed"] == 0
    assert archive_history.head(root).subject == "Fix Rotation: batches 1 and 2"
    assert f"scans/{PAGE_1}" in archive_history.tracked_paths(root)    # the scan as turned
    left = archive_history.changed_paths(root)
    assert "scans/dropped in, not indexed.png" in left                  # not indexed: File Index's
    assert "archive/decisions.json" in left                             # Review's, not a rotation file
    assert not {"archive/rotation_decisions.json", "archive/rotation_log.jsonl"} & left   # the decision went too
    assert ingest_client.post("/api/ingest/finalize/rotation", json={}).json()["committed"] is None

    group = ingest_client.post("/api/ingest/finalize/group", json={}).json()
    assert group["committed"] and archive_history.head(root).subject == "Group: batches 1 and 2"
    assert "archive/decisions.json" not in archive_history.changed_paths(root)   # a whole file: Review's went too
    assert ingest_client.post("/api/ingest/finalize/nope", json={}).status_code == 422


def test_the_queue_holds_the_scans_that_look_turned_until_they_are_decided(ingest_client, configured_ingest, tmp_path):
    scans = tmp_path / "scans"
    printed(top_points="down").save(scans / PAGE_1)                                   # 1:1
    printed("flyer.png").save(scans / "01102025140000_3.png")                          # 1:3, upright
    printed("flyer.png", "right").save(scans / "01102025141500_4.png")                 # 1:4
    printed(top_points="left").save(scans / "01102025142000_6.png")                    # 1:6 is tossed
    untouched = (scans / PAGE_1).read_bytes()

    found = rotation_queue(ingest_client)
    assert found["checked"] and [(i["key"], i["prediction"]["turn"]) for i in found["items"]] == [("1:1", "down"),
                                                                                                ("1:4", "right")]
    assert all(orientation.MIN_CONFIDENCE <= i["prediction"]["confidence"] <= 1 for i in found["items"])
    assert all(i["prediction"]["method"] == "v1" and not i["sent_back"] for i in found["items"])
    versions = {p["key"]: p["image_version"] for p in ingest_client.get("/api/ingest/grouping").json()["pages"]}
    assert all(i["image_version"] == versions[i["key"]] for i in found["items"])      # measured on this version
    assert (scans / PAGE_1).read_bytes() == untouched                                  # asking changes nothing

    fixed = decide(ingest_client, "1:1", PAGE_1, top="down").json()
    assert fixed["action"] == "fixed" and fixed["agrees"] and fixed["predicted"]["turn"] == "down"
    left = decide(ingest_client, "1:4", "01102025141500_4.png", fix=False).json()
    assert left["action"] == "left" and left["agrees"] is False                       # a false positive
    after = rotation_queue(ingest_client)
    assert after["items"] == [] and (after["fixed"], after["left"]) == (1, 1)
    assert [r["decision"]["key"] for r in after["recent"]] == ["1:4", "1:1"]           # newest first
    assert [d.key for d in load_rotation_log(configured_ingest)] == ["1:1", "1:4"]     # the training data
    assert ingest_client.get("/api/ingest/counts").json()["rotation"] == 0


def test_a_decision_is_on_the_version_the_page_showed(ingest_client, configured_ingest, tmp_path):
    printed(top_points="down").save(tmp_path / "scans" / PAGE_1)
    version = (tmp_path / "scans" / PAGE_1).stat().st_mtime_ns
    stale = ingest_client.post("/api/ingest/rotation/decide", json={"key": "1:1", "image_version": version - 10_000,
                                                                    "fix": True, "top_points": "down"})
    assert stale.status_code == 409 and "changed" in stale.json()["detail"]
    nothing = ingest_client.post("/api/ingest/rotation/decide", json={"key": "1:1", "image_version": version,
                                                                      "fix": True})
    # a browser holds the version (an mtime in nanoseconds) as a double: it comes back rounded
    rounded = int(float(version)) + 100
    assert ingest_client.post("/api/ingest/rotation/decide", json={"key": "1:1", "image_version": rounded,
                                                                   "fix": False}).status_code == 200
    assert rotation_queue(ingest_client)["items"] == []                               # decided on as it is
    assert nothing.status_code == 409                          # a fix turns or straightens something
    for key in ("1:2", "1:99"):
        assert decide(ingest_client, key, PAGE_1, top="down").status_code == 404
    assert [d.action for d in load_rotation_log(configured_ingest)] == ["left"]


def test_a_scan_that_cant_be_read_doesnt_stop_the_others_being_checked(ingest_client, configured_ingest, tmp_path):
    scans = tmp_path / "scans"
    printed(top_points="down").save(scans / PAGE_1)                                   # 1:1
    (scans / "01102025140000_3.png").write_bytes(b"")                                 # 1:3, half copied
    (scans / "01102025141500_4.png").write_bytes(b"not a png")                        # 1:4
    assert [i["key"] for i in rotation_queue(ingest_client)["items"]] == ["1:1"]


def test_the_queue_says_when_the_model_cannot_be_had_after_one_try(ingest_client, configured_ingest, tmp_path,
                                                                    monkeypatch):
    for name in ("01102025140000_3.png", "01102025141500_4.png"):
        printed().save(tmp_path / "scans" / name)
    tilt_fixture("receipt_crooked.png").save(tmp_path / "scans" / PAGE_1)
    tries = []

    def unavailable(url=orientation.MODEL_URL):
        tries.append(url)
        raise orientation.ModelUnavailable("couldn't download the orientation model: no network")
    monkeypatch.setattr(orientation, "_net", None)            # not loaded yet
    monkeypatch.setattr(orientation, "fetch_model", unavailable)
    found = rotation_queue(ingest_client)
    assert found["checked"] is False and [i["key"] for i in found["items"]] == ["1:1"]   # tilts are still found
    assert found["items"][0]["prediction"]["confidence"] is None
    assert len(tries) == 1                                    # not once per page


def test_toss_recover_and_rotate(ingest_client, configured_ingest, tmp_path):
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:5"}).status_code == 200
    assert load_decisions(configured_ingest)["1:4-5"].verdict == "tossed"
    assert ingest_client.post("/api/ingest/pages/recover", json={"key": "1:5"}).status_code == 200
    assert "1:4-5" not in load_decisions(configured_ingest)
    assert ingest_client.post("/api/ingest/pages/toss", json={"key": "1:99"}).status_code == 404

    before = next(p for p in ingest_client.get("/api/ingest/grouping").json()["pages"] if p["key"] == "1:1")
    with Image.open(tmp_path / "scans" / PAGE_1) as img:
        width, height = img.size
    assert decide(ingest_client, "1:1", PAGE_1, top="left").status_code == 200
    with Image.open(tmp_path / "scans" / PAGE_1) as img:
        assert img.size == (height, width)
    after = next(p for p in ingest_client.get("/api/ingest/grouping").json()["pages"] if p["key"] == "1:1")
    assert after["image_version"] != before["image_version"]


def test_tilted_pages_are_suggested_and_straightened_only_on_request(ingest_client, configured_ingest, tmp_path):
    scans = tmp_path / "scans"
    tilt_fixture("receipt_crooked.png").save(scans / PAGE_1)                           # 1:1, fed crooked
    tilt_fixture("receipt_level.png").save(scans / "01102025140000_3.png")             # 1:3, level
    tilt_fixture("long_receipt_crooked.png").save(scans / "01102025142000_6.png")      # 1:6 is tossed
    untouched = (scans / PAGE_1).read_bytes()

    [item] = rotation_queue(ingest_client)["items"]
    assert item["key"] == "1:1" and item["prediction"]["turn"] is None
    assert item["prediction"]["tilt"] == pytest.approx(3.7, abs=0.2)
    assert (scans / PAGE_1).read_bytes() == untouched                                  # asking changes nothing
    level = ingest_client.get("/api/ingest/rotation/prediction", params={"key": "1:3"}).json()
    assert level["tilt"] is None and abs(level["degrees"]) < 0.8                      # any page, flagged or not
    assert ingest_client.get("/api/ingest/rotation/prediction", params={"key": "1:99"}).status_code == 404
    outline = ingest_client.get("/api/ingest/pages/ink-outline", params={"key": "1:1"}).json()["points"]
    with Image.open(scans / PAGE_1) as img:
        assert outline == [list(p) for p in deskew.ink_outline(img)] and len(outline) >= 3   # for the preview's crop

    assert decide(ingest_client, "1:1", PAGE_1, degrees=90).status_code == 422
    fixed = decide(ingest_client, "1:1", PAGE_1, degrees=item["prediction"]["tilt"] + 0.2).json()
    assert fixed["agrees"]                                                            # within 0.3 of the suggestion
    assert rotation_queue(ingest_client)["items"] == []


def test_a_scan_both_turned_and_crooked_is_measured_turned_and_fixed_in_one_step(ingest_client, configured_ingest,
                                                                              tmp_path):
    """Its lines run down the page, so its tilt is measured on it as the turn will leave it; one Fix turns it
    and then straightens it by that."""
    crooked = tilt_fixture("receipt_crooked.png")
    crooked.transpose(Image.Transpose.ROTATE_90).save(tmp_path / "scans" / PAGE_1)   # its top points left

    [item] = rotation_queue(ingest_client)["items"]
    assert item["prediction"]["turn"] == "left" and item["prediction"]["tilt"] == pytest.approx(3.7, abs=0.2)
    fixed = decide(ingest_client, "1:1", PAGE_1, top="left", degrees=item["prediction"]["tilt"]).json()
    assert fixed["agrees"]
    with Image.open(tmp_path / "scans" / PAGE_1) as img:
        assert img.height > img.width                                                 # upright
        assert abs(deskew.estimate_skew(img).degrees) < 0.5                           # and level


def test_undo_restores_the_scan_and_its_trim_from_the_history(ingest_client, configured_ingest, tmp_path):
    import archive_history
    from data import load_trims, set_trim
    from models import Trim

    scans = tmp_path / "scans"
    printed(top_points="down").save(scans / PAGE_1)
    archive_history.ensure_repository(configured_ingest.parent)
    original = (scans / PAGE_1).read_bytes()
    band = Trim(top=0.1, bottom=0.6)
    set_trim(configured_ingest, "1:1", band)

    fixed = decide(ingest_client, "1:1", PAGE_1, top="down").json()
    assert fixed["before"] and fixed["trim_before"] == band.model_dump()
    assert (scans / PAGE_1).read_bytes() != original and load_trims(configured_ingest)["1:1"] != band
    assert rotation_queue(ingest_client)["recent"][0]["undoable"]

    undone = ingest_client.post("/api/ingest/rotation/undo", json={"key": "1:1"})
    assert undone.status_code == 200 and undone.json()["action"] == "undone"
    assert (scans / PAGE_1).read_bytes() == original and load_trims(configured_ingest)["1:1"] == band
    assert "1:1" not in load_ocr_results(configured_ingest)                          # read again, as it is now
    after = rotation_queue(ingest_client)
    assert [i["key"] for i in after["items"]] == ["1:1"]                              # and judged again
    assert after["fixed"] == 0                                                        # a fix undone isn't one
    again = ingest_client.post("/api/ingest/rotation/undo", json={"key": "1:1"})
    assert again.status_code == 409                                                   # its last decision isn't a fix
    assert [d.action for d in load_rotation_log(configured_ingest)] == ["fixed", "undone"]


def test_undo_is_refused_when_the_scan_changed_or_wasnt_kept_and_leaves_no_swap_file(
        ingest_client, configured_ingest, tmp_path, monkeypatch):
    import archive_history
    import rotation_review

    scans = tmp_path / "scans"
    printed(top_points="down").save(scans / PAGE_1)
    fixed = decide(ingest_client, "1:1", PAGE_1, top="down").json()                   # no history yet
    assert fixed["before"] is None and not rotation_queue(ingest_client)["recent"][0]["undoable"]
    refused = ingest_client.post("/api/ingest/rotation/undo", json={"key": "1:1"})
    assert refused.status_code == 409 and "history" in refused.json()["detail"]

    archive_history.ensure_repository(configured_ingest.parent)
    printed(top_points="left").save(scans / "01102025140000_3.png")                   # 1:3
    assert decide(ingest_client, "1:3", "01102025140000_3.png", top="left").status_code == 200
    fixed_scan = (scans / "01102025140000_3.png").read_bytes()

    def busy(src, dst):
        raise PermissionError("the scan is open elsewhere")
    with monkeypatch.context() as patch:
        patch.setattr(rotation_review.os, "replace", busy)
        refused = ingest_client.post("/api/ingest/rotation/undo", json={"key": "1:3"})
    assert refused.status_code == 409 and "open elsewhere" in refused.json()["detail"]
    assert (scans / "01102025140000_3.png").read_bytes() == fixed_scan
    assert not list(scans.glob("*.rotating.*"))                     # nothing File Index would take for a scan

    printed().save(scans / "01102025140000_3.png")                                    # changed since the fix
    refused = ingest_client.post("/api/ingest/rotation/undo", json={"key": "1:3"})
    assert refused.status_code == 409 and "changed since" in refused.json()["detail"]
    assert [d.action for d in load_rotation_log(configured_ingest)] == ["fixed", "fixed"]


def test_a_page_sent_back_stays_in_the_queue_when_its_fix_is_undone(ingest_client, configured_ingest, tmp_path):
    import archive_history

    tilt_fixture("receipt_level.png").save(tmp_path / "scans" / PAGE_1)               # nothing to flag
    archive_history.ensure_repository(configured_ingest.parent)
    assert ingest_client.post("/api/review/send-back", json={"key": "1:1"}).status_code == 200
    assert decide(ingest_client, "1:1", PAGE_1, degrees=1.2).status_code == 200
    assert rotation_queue(ingest_client)["items"] == []
    assert ingest_client.post("/api/ingest/rotation/undo", json={"key": "1:1"}).status_code == 200
    [item] = rotation_queue(ingest_client)["items"]                  # someone said it needs fixing: it still does
    assert item["key"] == "1:1" and item["sent_back"]


def test_review_sends_back_a_page_the_detectors_missed(ingest_client, configured_ingest, tmp_path):
    from data import EXTRACTION_RUNS, OCR_RUNS, load_model_runs, merge_model_runs
    from models import ModelRun

    tilt_fixture("receipt_level.png").save(tmp_path / "scans" / PAGE_1)               # nothing to flag
    pages = ingest_client.get("/api/review/document", params={"key": "1:1"}).json()["pages"]
    assert pages[0]["turnable"]                                      # Review offers to send it back
    merge_model_runs(configured_ingest, OCR_RUNS, {"1:1": ModelRun(model="m", at=0, seconds=1)})
    assert rotation_queue(ingest_client)["items"] == []

    sent = ingest_client.post("/api/review/send-back", json={"key": "1:1"})
    assert sent.status_code == 200
    record = sent.json()
    assert (record["action"], record["source"], record["agrees"]) == ("sent_back", "review", False)
    assert record["predicted"]["tilt"] is None and record["predicted"]["turn"] is None   # what they missed
    assert "1:1" not in load_ocr_results(configured_ingest) and "1:1" not in load_extractions(configured_ingest)
    assert "1:1" not in load_model_runs(configured_ingest, OCR_RUNS)
    [item] = rotation_queue(ingest_client)["items"]
    assert item["key"] == "1:1" and item["sent_back"]
    assert decide(ingest_client, "1:1", PAGE_1, degrees=1.2).json()["agrees"] is False   # a false negative, fixed
    assert rotation_queue(ingest_client)["items"] == []
    assert ingest_client.post("/api/review/send-back", json={"key": "1:99"}).status_code == 404


def test_the_sidebar_counts_agree_with_each_step(ingest_client, configured_ingest, tmp_path):
    scans = tmp_path / "scans"
    printed(top_points="down").save(scans / PAGE_1)                                    # 1:1, upside down
    tilt_fixture("receipt_crooked.png").save(scans / "01102025140000_3.png")           # 1:3, fed crooked
    Image.new("RGB", (4, 4)).save(scans / "01112025090100_1.png")                      # not in a batch yet

    counts = ingest_client.get("/api/ingest/counts").json()
    assert counts["unindexed"] == ingest_client.get("/api/ingest/index").json()["unindexed_count"] == 1
    assert counts["rotation_checked"] and counts["rotation"] == len(rotation_queue(ingest_client)["items"]) == 2
    ocr = ingest_client.get("/api/ingest/ocr").json()
    parse = ingest_client.get("/api/ingest/parse").json()
    queue = ingest_client.get("/api/review/queue").json()
    assert counts["ocr"] == ocr["to_process"] + ocr["waiting"]
    assert counts["parse"] == parse["to_process"] + parse["waiting"]
    assert counts["review"] == queue["summary"]["pending"]
    archive = ingest_client.get("/api/ingest/archive").json()
    assert archive["blocker"] is None and counts["archive"] == archive["documents"] > 0     # the batch is reviewed

    assert decide(ingest_client, "1:1", PAGE_1, top="down").status_code == 200
    assert ingest_client.get("/api/ingest/counts").json()["rotation"] == 1
    decisions = load_decisions(configured_ingest)
    del decisions["1:3"]
    save_decisions(configured_ingest, decisions)
    counts = ingest_client.get("/api/ingest/counts").json()
    assert counts["archive"] == 0 and counts["review"] == ingest_client.get("/api/review/queue").json()["summary"]["pending"]


def test_the_sidebar_counts_never_download_the_orientation_model(ingest_client, configured_ingest, tmp_path,
                                                                  monkeypatch):
    printed(top_points="down").save(tmp_path / "scans" / PAGE_1)                      # 1:1, turned
    tilt_fixture("receipt_crooked.png").save(tmp_path / "scans" / "01102025140000_3.png")   # 1:3, tilted

    def download(url=orientation.MODEL_URL):
        pytest.fail("the sidebar downloaded the orientation model")
    monkeypatch.setattr(orientation, "_net", None)
    monkeypatch.setattr(orientation, "model_path", lambda: tmp_path / "not downloaded.onnx")
    monkeypatch.setattr(orientation, "fetch_model", download)
    counts = ingest_client.get("/api/ingest/counts")
    assert counts.status_code == 200
    assert counts.json()["rotation_checked"] is False                               # tilts only, until Fix Rotation
    assert counts.json()["rotation"] == 1


def test_the_sidebar_counts_tilts_when_the_orientation_model_cannot_be_loaded(ingest_client, configured_ingest,
                                                                             tmp_path, monkeypatch):
    printed(top_points="down").save(tmp_path / "scans" / PAGE_1)
    broken = tmp_path / "broken.onnx"
    broken.write_bytes(b"not a model")
    monkeypatch.setattr(orientation, "_net", None)
    monkeypatch.setattr(orientation, "model_path", lambda: broken)
    counts = ingest_client.get("/api/ingest/counts")
    assert counts.status_code == 200
    assert counts.json()["rotation_checked"] is False and counts.json()["rotation"] == 0


@pytest.mark.parametrize("turn", [{"degrees": 3.7}, {"top": "down"}])
def test_turning_a_read_page_sends_it_back_to_ocr_and_parse(ingest_client, configured_ingest, tmp_path, turn):
    """What was read from the scan the old way no longer holds: its OCR and its document's extraction go,
    with their run records, and OCR and Parse take the page up again. A review decision is a person's, and
    every other page keeps what was read from it."""
    from data import EXTRACTION_RUNS, OCR_RUNS, load_model_runs, merge_model_runs
    from models import ModelRun

    tilt_fixture("receipt_crooked.png").save(tmp_path / "scans" / PAGE_1)
    merge_model_runs(configured_ingest, OCR_RUNS, {"1:1": ModelRun(model="m", at=0, seconds=1)})
    merge_model_runs(configured_ingest, EXTRACTION_RUNS, {"1:1": ModelRun(model="m", at=0, seconds=1)})
    everything = load_ocr_results(configured_ingest)
    extractions = load_extractions(configured_ingest)
    decisions = load_decisions(configured_ingest)
    assert "1:1" in everything and "1:1" in extractions, "the fixture's page 1:1 is read and parsed"
    assert ingest_client.get("/api/ingest/ocr").json()["to_process"] == 0

    assert decide(ingest_client, "1:1", PAGE_1, **turn).status_code == 200
    after = load_ocr_results(configured_ingest)
    assert "1:1" not in after and "1:1" not in load_extractions(configured_ingest)
    assert "1:1" not in load_model_runs(configured_ingest, OCR_RUNS)
    assert "1:1" not in load_model_runs(configured_ingest, EXTRACTION_RUNS)
    assert ingest_client.get("/api/ingest/ocr").json()["to_process"] == 1          # read again next run
    assert load_decisions(configured_ingest) == decisions
    assert {k: v for k, v in after.items() if k != "1:1"} == {k: v for k, v in everything.items() if k != "1:1"}
    assert {k: v for k, v in load_extractions(configured_ingest).items()} ==         {k: v for k, v in extractions.items() if k != "1:1"}


def test_straightening_a_trimmed_page_moves_its_trim(ingest_client, configured_ingest, tmp_path):
    from data import load_trims, set_trim
    from models import Trim

    tilt_fixture("receipt_crooked.png").save(tmp_path / "scans" / PAGE_1)
    band = Trim(top=0.1, bottom=0.8)
    set_trim(configured_ingest, "1:1", band)

    assert decide(ingest_client, "1:1", PAGE_1, degrees=3.7).status_code == 200
    trim = load_trims(configured_ingest)["1:1"]
    assert trim != band and trim.top < band.top + 0.05 and trim.bottom > band.bottom - 0.05   # keeps all it held


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


def test_a_parse_run_on_a_billed_model_adds_up_what_it_is_spending(ingest_client, configured_ingest, monkeypatch):
    def extract(text, has_boxes=False, custom_instruction="", on_usage=None):
        on_usage(TokenUse(prompt=2000, cached=1024, completion=400, thinking=250))
        return _receipt("Parsed")

    monkeypatch.setattr(ingest_registry, "extractors", lambda: {"OpenAI - gpt-6-luna": extract})
    monkeypatch.setattr(ingest_registry, "unload_extractor", lambda name: lambda: None)

    job = ingest_client.post("/api/ingest/parse", json={"extractor": "OpenAI - gpt-6-luna", "reprocess": True,
                                                        "limit": 2}).json()
    done = _finish(job)

    assert done["done"] == 2
    assert done["spent"] == pytest.approx(2 * 0.000308, abs=1e-5)     # both calls, at Luna's prices
    assert (done["calls"], done["prompt_tokens"], done["cached_tokens"]) == (2, 4000, 2048)
    assert (done["completion_tokens"], done["thinking_tokens"]) == (800, 500)


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
    assert decide(ingest_client, "2:1", "01112025090100_1.png", top="left").status_code == 200
    assert _finalize_group(ingest_client, (2, [])).status_code == 200
    assert _finalize_group(ingest_client, (1, [])).json()["detail"] == \
        "Can't change document grouping while OCR with Fake OCR is using batch 1."
    refused = decide(ingest_client, "1:1", PAGE_1, top="left")
    assert refused.status_code == 409 and refused.json()["detail"] == \
        "Can't turn scans while OCR with Fake OCR is using batch 1."
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
    assert status["blocker"] is None and (status["files"], status["tossed"], status["scans_to_remove"]) == (8, 2, 0)
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


# --- the folder's history -----------------------------------------------------------------------------------
def test_file_index_says_when_its_batches_were_added_but_not_committed(ingest_client, configured_ingest, tmp_path,
                                                                    monkeypatch):
    import archive_history

    monkeypatch.setattr(archive_history, "GIT", "git-that-is-not-installed")
    refused = _add_batch_2(ingest_client, tmp_path / "scans")
    assert refused.status_code == 500
    assert refused.json()["detail"].startswith("The batches were added, but not committed to the folder's history: git")
    assert [b.batch_id for b in load_scan_index(configured_ingest).batches] == [1, 2]


def test_a_job_whose_commit_fails_still_succeeds_and_says_so(ingest_client, configured_ingest, fake_ocr, monkeypatch):
    import archive_history

    monkeypatch.setattr(archive_history, "GIT", "git-that-is-not-installed")
    job = ingest_client.post("/api/ingest/ocr", json={"provider": "Fake OCR", "reprocess": True}).json()
    done = _finish(job)
    assert done["status"] == "succeeded" and "Not committed to the folder's history: git" in done["message"]


def test_the_sidebar_s_count_and_a_manual_commit(ingest_client, configured_ingest):
    import archive_history

    root = configured_ingest.parent
    before = ingest_client.get("/api/history").json()
    assert before["repository"] is False and before["changed"] == 0 and before["last"] is None

    draft = {"document_type": "receipt", "name": "Shop", "date": "2025-01-10", "time": "10:00", "cost": 100,
             "currency": "JPY"}
    assert ingest_client.post("/api/review/decisions", json={"key": "1:3", "verdict": "accepted", "draft": draft}).status_code == 200
    assert ingest_client.get("/api/history").json()["repository"] is False           # a decision is no milestone

    done = ingest_client.post("/api/history/commit", json={"message": "  "}).json()
    assert done["repository"] and done["changed"] == 0
    assert done["last"]["subject"] == archive_history.MANUAL and done["last"]["seconds_ago"] < 60
    assert "archive/decisions.json" in archive_history.tracked_paths(root)

    (configured_ingest / "decisions.json").write_bytes(b"{}")
    assert ingest_client.get("/api/history").json()["changed"] == 1
    named = ingest_client.post("/api/history/commit", json={"message": "reviewed batch 1"}).json()
    assert named["last"]["subject"] == "reviewed batch 1" and named["changed"] == 0


def test_the_history_page_lists_commits_and_changes_and_takes_them_back(ingest_client, configured_ingest):
    notes = [{"path": "archive/notes.json", "kind": "added", "lines_added": 1, "lines_removed": 0}]
    assert ingest_client.get("/api/history/changes").json() == []                       # no history yet
    assert ingest_client.get("/api/history/commits").json() == {"commits": [], "more": False}
    ingest_client.post("/api/history/commit", json={"message": "everything"})
    (configured_ingest / "notes.json").write_bytes(b"{}")
    assert ingest_client.get("/api/history/changes").json() == notes
    ingest_client.post("/api/history/commit", json={"message": "reviewed"})

    listed = ingest_client.get("/api/history/commits", params={"limit": 1}).json()
    assert listed["more"] and [(c["subject"], c["files"], c["first"]) for c in listed["commits"]] == [("reviewed", 1, False)]
    last = listed["commits"][0]["sha"]
    assert ingest_client.get(f"/api/history/commits/{last}").json() == notes
    assert ingest_client.get("/api/history/commits/0000000").status_code == 404

    stale = ingest_client.post("/api/history/uncommit", json={"sha": "0000000"})
    assert stale.status_code == 409 and "“reviewed”" in stale.json()["detail"]
    undone = ingest_client.post("/api/history/uncommit", json={"sha": last}).json()
    assert undone["last"]["subject"] == "everything" and undone["changed"] == 1
    assert (configured_ingest / "notes.json").read_bytes() == b"{}"                 # the file stays

    release = threading.Event()
    job = runner.start("ocr", "OCR (test)", lambda progress: release.wait(10), Claim(batches=frozenset({1})))
    try:
        busy = ingest_client.post("/api/history/discard-changes", json={"paths": ["archive/notes.json"]})
        assert busy.status_code == 409 and "OCR (test)" in busy.json()["detail"]
    finally:
        release.set()
        runner.wait_until_finished(job["id"])
    thrown = ingest_client.post("/api/history/discard-changes", json={"paths": ["archive/notes.json"]}).json()
    assert thrown["changed"] == 0 and not (configured_ingest / "notes.json").exists()


def test_the_api_parks_what_is_uncommitted_when_it_stops_and_takes_it_back_when_it_starts(configured_ingest):
    import archive_history
    from fastapi.testclient import TestClient

    from api.main import create_app

    root = configured_ingest.parent
    with TestClient(create_app()):
        (configured_ingest / "decisions.json").write_bytes(b"{}")
    assert not (root / ".git").exists()             # no history yet: a shutdown doesn't start one
    archive_history.ensure_repository(root)
    with TestClient(create_app()):
        pass
    parked = archive_history.head(root)
    assert parked is not None and parked.subject == archive_history.PARKED
    assert archive_history.status(root).changed == 0

    with TestClient(create_app()):
        assert archive_history.head(root).subject == "History started"                # the parking commit is undone
        assert "archive/decisions.json" in archive_history.changed_paths(root)      # and its changes wait again
    assert archive_history.head(root).subject == archive_history.PARKED             # parked once more on the way out


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


def test_trimming_a_page_shows_in_grouping_and_puts_it_back_in_the_ocr_queue(ingest_client, configured_ingest):
    def page(key):
        return next(p for p in ingest_client.get("/api/ingest/grouping").json()["pages"] if p["key"] == key)

    assert page("1:1")["trim"] is None
    assert ingest_client.get("/api/ingest/ocr").json()["retrimmed"] == 0

    trimmed = ingest_client.put("/api/ingest/pages/trim", json={"key": "1:1", "trim": {"top": 0, "bottom": 0.75}})

    assert trimmed.json() == {"changed": True}
    assert page("1:1")["trim"] == {"top": 0.0, "bottom": 0.75}
    status = ingest_client.get("/api/ingest/ocr").json()
    assert (status["retrimmed"], status["to_process"]) == (1, 1)       # 1:1 is the one scan in the input folder
    ingest_client.put("/api/ingest/pages/trim", json={"key": "1:1", "trim": None})
    assert page("1:1")["trim"] is None and ingest_client.get("/api/ingest/ocr").json()["to_process"] == 0


def test_a_trim_has_to_keep_some_of_a_page_that_exists(ingest_client):
    assert ingest_client.put("/api/ingest/pages/trim",
                             json={"key": "1:1", "trim": {"top": 0.5, "bottom": 0.5}}).status_code == 422
    assert ingest_client.put("/api/ingest/pages/trim",
                             json={"key": "1:99", "trim": {"top": 0, "bottom": 0.5}}).status_code == 404


def test_a_sliced_sheet_cant_be_trimmed_but_its_crops_can(ingest_client, configured_ingest, tmp_path):
    import slicing
    from models import Box, SheetGrid

    Image.new("RGB", (200, 100), "white").save(tmp_path / "scans" / PAGE_1)    # a sheet of two tickets
    two = SheetGrid(frame=Box(x1=0, y1=0, x2=1000, y2=1000), col_lines=[500], cells=[[1, 1], [1, 2]])
    plan = slicing.plan_slices(configured_ingest, 1, 1, two)
    slicing.apply_slices(configured_ingest, tmp_path / "scans", 1, 1, two, plan.token)
    band = {"top": 0, "bottom": 0.5}

    refused = ingest_client.put("/api/ingest/pages/trim", json={"key": "1:1", "trim": band})
    assert refused.status_code == 409 and "trim a crop instead" in refused.json()["detail"]
    assert ingest_client.put("/api/ingest/pages/trim", json={"key": "1:9", "trim": band}).status_code == 200
    pages = {p["key"]: p for p in ingest_client.get("/api/ingest/grouping").json()["pages"]}
    assert pages["1:9"]["trim"] == {"top": 0.0, "bottom": 0.5} and pages["1:1"]["trim"] is None
