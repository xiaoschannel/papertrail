"""Ingest pipeline steps against the mid-ingest fixture, with fake OCR/extraction models."""

import json
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

import ingest_pipeline as ip
from data import (
    load_decisions, load_document_groups, load_extractions, load_ocr_results, load_smart_match_cache, save_decisions,
    load_ocr_batch, save_ocr_batch, save_ocr_results,
)
from models import OcrResult, ReceiptResult, TokenUse, iter_indexed_files, load_scan_index

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeProgress:
    def __init__(self, cancel_after: int | None = None):
        self.total = None
        self.ticks: list[tuple[bool, str, str]] = []
        self.runs: list = []
        self.cancel_after = cancel_after

    @property
    def cancelled(self) -> bool:
        return self.cancel_after is not None and len(self.ticks) >= self.cancel_after

    @property
    def job_id(self) -> str:
        return "test-job"

    def set_total(self, total):
        self.total = total

    def tick(self, ok=True, item="", error=""):
        self.ticks.append((ok, item, error))

    def say(self, message):
        pass

    def record(self, run):
        self.runs.append(run)


class FakeOcr:
    grounding = True

    def __init__(self, fail_on: str = ""):
        self.calls: list[tuple[str, bool]] = []
        self.fail_on = fail_on

    def run(self, path, structured=False):
        self.calls.append((path.name, structured))
        if self.fail_on and path.name == self.fail_on:
            raise RuntimeError("scanner smudge")
        if structured:
            return "<|ref|>合計<|/ref|><|det|>[[10, 20, 30, 40]]<|/det|>"
        return f"text of {path.name}"


def _scans(tmp_path: Path, ingest_dir: Path) -> Path:
    scans = tmp_path / "scans"
    scans.mkdir(exist_ok=True)
    for _, _, fn in iter_indexed_files(load_scan_index(ingest_dir)):
        Image.new("RGB", (40, 80), "white").save(scans / fn)
    return scans


def _receipt(name="Shop") -> ReceiptResult:
    return ReceiptResult(document_type="receipt", language="ja", date="2025-01-10", time="10:00", name=name,
                         currency="JPY", address="", cost=100.0)


# --- File Index ----------------------------------------------------------------------------------
def test_propose_and_confirm_new_batch(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    for serial in (1, 2, 3):
        Image.new("RGB", (4, 4)).save(scans / f"01112025090{serial}00_{serial}.png")

    proposal = ip.propose_index(scans, ingest_dir, "Canon ImageFormula")
    assert (proposal.image_count, proposal.indexed_count, proposal.unindexed_count) == (11, 8, 3)
    [batch] = proposal.batches
    assert batch.batch_id == 2 and sorted(batch.files) == [1, 2, 3]
    assert proposal.error is None and proposal.token == ip.propose_index(scans, ingest_dir, "Canon ImageFormula").token

    with pytest.raises(ip.StaleProposal):
        ip.confirm_index(scans, ingest_dir, "Canon ImageFormula", "not-the-token")
    ip.confirm_index(scans, ingest_dir, "Canon ImageFormula", proposal.token)
    assert [b.batch_id for b in load_scan_index(ingest_dir).batches] == [1, 2]
    assert ip.propose_index(scans, ingest_dir, "Canon ImageFormula").batches == []


def test_propose_index_flags_files_older_than_the_last_batch(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    Image.new("RGB", (4, 4)).save(scans / "01092025090000_1.png")  # before the fixture batch
    proposal = ip.propose_index(scans, ingest_dir, "Canon ImageFormula")
    assert proposal.offending == ["01092025090000_1.png"] and proposal.batches == []
    assert "Delete batches.json" in proposal.error
    with pytest.raises(ValueError):
        ip.confirm_index(scans, ingest_dir, "Canon ImageFormula", proposal.token)


# --- grouping --------------------------------------------------------------------------------------
def test_grouping_state_reflects_saved_groups_and_tossed_pages(ingest_dir):
    state = ip.grouping_state(ingest_dir, 1)
    assert state.saved_groups == [["1:4", "1:5"]]
    assert [p.key for p in state.pages if p.tossed] == ["1:6", "1:8"]
    assert state.active_links == [False, False, False, True, False]  # 1:4 links to 1:5 among active pages


def test_save_grouping_only_clears_when_groups_change(ingest_dir):
    assert ip.save_grouping(ingest_dir, 1, [["1:1"], ["1:2"], ["1:3"], ["1:5", "1:4"][::-1], ["1:7"]]) is False
    assert "1:1" in load_extractions(ingest_dir)

    assert ip.save_grouping(ingest_dir, 1, [["1:1", "1:2"], ["1:3"], ["1:4", "1:5"], ["1:7"]]) is True
    assert sorted(load_document_groups(ingest_dir).groups) == [["1:1", "1:2"], ["1:4", "1:5"]]
    assert load_extractions(ingest_dir) == {}
    # the batch's documents must be re-parsed and re-reviewed; tosses stay (see test_data_stores)
    assert {k: d.verdict for k, d in load_decisions(ingest_dir).items()} == {"1:6": "tossed", "1:8": "tossed"}


def test_tossing_a_saved_group_is_not_a_grouping_change(ingest_dir):
    ip.set_page_tossed(ingest_dir, "1:5", True)  # tosses document 1:4-5
    state = ip.grouping_state(ingest_dir, 1)
    assert state.saved_groups == []  # what the editor shows: no active multi-page groups
    unedited = [["1:1"], ["1:2"], ["1:3"], ["1:7"]]
    assert ip.save_grouping(ingest_dir, 1, unedited) is False
    assert load_decisions(ingest_dir)["1:2"].verdict == "accepted"

    # a real change elsewhere keeps the tossed document's group and its toss
    assert ip.save_grouping(ingest_dir, 1, [["1:1", "1:2"], ["1:3"], ["1:7"]]) is True
    assert sorted(load_document_groups(ingest_dir).groups) == [["1:1", "1:2"], ["1:4", "1:5"]]
    assert {k for k, d in load_decisions(ingest_dir).items() if d.verdict == "tossed"} == {"1:4-5", "1:6", "1:8"}
    assert [p.key for p in ip.grouping_state(ingest_dir, 1).pages if p.tossed] == ["1:4", "1:5", "1:6", "1:8"]


@pytest.mark.parametrize("groups, message", [
    ([["1:6", "1:7"]], "not an active page"),     # 1:6 is tossed
    ([["1:1", "1:2"], ["1:2"]], "more than one group"),
    ([["9:1"]], "not an active page"),
])
def test_save_grouping_rejects_invalid_groups(ingest_dir, groups, message):
    with pytest.raises(ValueError, match=message):
        ip.save_grouping(ingest_dir, 1, groups)


def test_toss_and_recover_a_page_of_a_multi_page_document(ingest_dir):
    ip.set_page_tossed(ingest_dir, "1:5", True)
    assert load_decisions(ingest_dir)["1:4-5"].verdict == "tossed"
    ip.set_page_tossed(ingest_dir, "1:5", False)
    assert "1:4-5" not in load_decisions(ingest_dir)
    with pytest.raises(KeyError):
        ip.set_page_tossed(ingest_dir, "1:99", True)


def test_rotate_page_image_turns_it_upright(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    ip.rotate_page_image(ingest_dir, scans, "1:1", "left")
    with Image.open(scans / "01102025132642_1.png") as img:
        assert img.size == (80, 40)


# --- OCR ----------------------------------------------------------------------------------------------
def test_plan_ocr_scopes(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    results = load_ocr_results(ingest_dir)
    del results["1:2"]
    results["1:3"] = OcrResult(markdown="boom", succeeded=False)
    save_ocr_results(ingest_dir, results)
    (scans / "01102025143000_8.png").unlink()

    plan = ip.plan_ocr(ingest_dir, scans, None, reprocess=False, limit=0)
    assert (plan.total, plan.processed, plan.failed, plan.missing_images) == (8, 6, 1, 1)
    assert [k for k, _ in plan.items] == ["1:2", "1:3"]
    assert len(ip.plan_ocr(ingest_dir, scans, None, reprocess=True, limit=0).items) == 7
    assert len(ip.plan_ocr(ingest_dir, scans, 1, reprocess=True, limit=3).items) == 3
    assert ip.plan_ocr(ingest_dir, scans, 2, reprocess=True, limit=0).total == 0


def test_run_ocr_keeps_other_results_and_records_failures(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    provider = FakeOcr(fail_on="01102025133000_2.png")
    items = [("1:1", scans / "01102025132642_1.png"), ("1:2", scans / "01102025133000_2.png")]
    progress = FakeProgress()

    ip.run_ocr(ingest_dir, items, provider, structured=True, progress=progress, model="Fake OCR", shuffle=False)

    results = load_ocr_results(ingest_dir)
    assert results["1:1"].markdown == "text of 01102025132642_1.png"
    assert results["1:1"].boxes[0].coords == [[10, 20, 30, 40]]
    assert not results["1:2"].succeeded
    assert results["1:3"].succeeded                    # untouched pages keep their results
    assert progress.total == 2 and [t[0] for t in progress.ticks] == [True, False]
    assert progress.ticks[1][2] == "RuntimeError: scanner smudge"


def test_run_ocr_without_grounding_runs_one_pass_and_stops_when_cancelled(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    provider = FakeOcr()
    items = [(f"1:{i}", scans / fn) for i, fn in [(1, "01102025132642_1.png"), (2, "01102025133000_2.png")]]
    progress = FakeProgress(cancel_after=1)
    ip.run_ocr(ingest_dir, items, provider, structured=False, progress=progress, model="Fake OCR", shuffle=False)
    assert provider.calls == [("01102025132642_1.png", False)]


def test_run_ocr_writes_only_the_batches_it_reads(ingest_dir, tmp_path):
    """A run holds its batches and no others: what another batch's owner changes meanwhile stays changed."""
    scans = _scans(tmp_path, ingest_dir)

    class EditsBatchOne(FakeOcr):
        def run(self, path, structured=False):
            batch_one = load_ocr_batch(ingest_dir, 1)
            batch_one.pop("1:3", None)                   # e.g. slicing batch 1 dropping a page's result
            save_ocr_batch(ingest_dir, 1, batch_one)
            return super().run(path, structured)

    items = [("2:1", scans / "01102025132642_1.png"), ("2:2", scans / "01102025133000_2.png")]
    ip.run_ocr(ingest_dir, items, EditsBatchOne(), structured=False, progress=FakeProgress(), model="Fake OCR",
               shuffle=False)

    assert "1:3" not in load_ocr_results(ingest_dir)
    assert sorted(load_ocr_batch(ingest_dir, 2)) == ["2:1", "2:2"]


# --- Parse -----------------------------------------------------------------------------------------------
def test_plan_parse(ingest_dir):
    extractions = load_extractions(ingest_dir)
    plan = ip.plan_parse(ingest_dir, reprocess=False, limit=0)
    assert (plan.total, plan.processed, plan.tossed, plan.documents) == (7, 5, 2, [])
    assert len(ip.plan_parse(ingest_dir, reprocess=True, limit=2).documents) == 2
    assert {str(d) for d in ip.plan_parse(ingest_dir, reprocess=True, limit=0).documents} == {
        "1:1", "1:2", "1:3", "1:4-5", "1:7"}  # tossed 1:6 and 1:8 are never parsed
    assert len(extractions) == 7


def test_run_parse_keeps_other_extractions_and_previous_result_on_failure(ingest_dir):
    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=0)
    plan.documents = [d for d in plan.documents if str(d) in ("1:1", "1:3")]

    def extract(ocr_text, has_boxes, custom_instruction):
        if "上海" in ocr_text:
            raise ValueError("model returned junk")
        assert custom_instruction == "Prefer Japanese names" and has_boxes
        return _receipt("Edited")

    progress = FakeProgress()
    ip.run_parse(ingest_dir, plan, extract, "Prefer Japanese names", progress, model="Fake LLM", shuffle=False)
    extractions = load_extractions(ingest_dir)
    assert extractions["1:1"].name == "Edited"
    assert extractions["1:3"].name == "上海小笼包馆"            # failure keeps the old extraction
    assert "1:6" in extractions and "1:8" in extractions        # tossed documents' extractions survive
    assert sorted(progress.ticks) == [(False, "1:3", "ValueError: model returned junk"), (True, "1:1", "")]


def test_a_hosted_run_extracts_several_documents_at_once(ingest_dir):
    """Four documents in flight, with the results and counts landing intact however they interleave."""
    import threading

    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=0)
    at_once, peak = 0, 0
    guard = threading.Lock()

    def extract(ocr_text, has_boxes, custom_instruction):
        nonlocal at_once, peak
        with guard:
            at_once += 1
            peak = max(peak, at_once)
        time.sleep(0.05)
        with guard:
            at_once -= 1
        return _receipt("Parsed at once")

    progress = FakeProgress()
    ip.run_parse(ingest_dir, plan, extract, "", progress, model="Fake LLM", workers=4, shuffle=False)

    assert peak > 1                                   # they really did overlap
    assert len(progress.ticks) == len(plan.documents)
    extractions = load_extractions(ingest_dir)
    assert all(extractions[str(d)].name == "Parsed at once" for d in plan.documents)


def test_a_run_refused_for_pacing_waits_and_tries_the_document_again(ingest_dir, monkeypatch):
    import httpx
    from openai import RateLimitError

    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=1)
    attempts = []
    monkeypatch.setattr(ip, "_sleep_while_running", lambda seconds, progress, **kw: attempts.append(seconds))

    def extract(ocr_text, has_boxes, custom_instruction):
        if len(attempts) < 2:                          # refused once, then allowed
            raise RateLimitError("slow down", body=None,
                                 response=httpx.Response(429, headers={"retry-after": "7"},
                                                         request=httpx.Request("POST", "https://x")))
        return _receipt("Parsed after waiting")

    progress = FakeProgress()
    ip.run_parse(ingest_dir, plan, extract, "", progress, model="Fake LLM", shuffle=False)

    assert [ok for ok, _, _ in progress.ticks] == [True]          # the document was not failed
    assert load_extractions(ingest_dir)[str(plan.documents[0])].name == "Parsed after waiting"
    assert attempts[-1] == pytest.approx(7, abs=1)                # it waited as long as it was told to


def test_a_call_that_failed_is_in_the_log_with_what_the_model_said(ingest_dir):
    """A job keeps its errors only until the next run of its kind; the log is where they last."""
    import run_log

    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=1)

    def extract(ocr_text, has_boxes, custom_instruction):
        raise ValueError("Unsupported value: 'nonsense' with this model")

    ip.run_parse(ingest_dir, plan, extract, "", FakeProgress(), model="Fake LLM", shuffle=False)

    [row] = run_log.read(ingest_dir)
    assert row["kind"] == "parse" and row["item"] == str(plan.documents[0])
    assert "Unsupported value" in row["error"]
    assert "cost" not in row                               # nothing was produced, so nothing was charged


def test_a_wait_longer_than_a_run_should_sit_through_ends_it_and_says_why(ingest_dir):
    """A minute's limit comes round; a day's quota does not, and 500 documents must not queue for it."""
    import httpx
    from openai import RateLimitError

    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=0)
    tried = []

    def extract(ocr_text, has_boxes, custom_instruction):
        tried.append(1)
        raise RateLimitError("out of quota", body=None,
                             response=httpx.Response(429, headers={"retry-after": "3600"},
                                                     request=httpx.Request("POST", "https://x")))

    progress = FakeProgress()
    message = ip.run_parse(ingest_dir, plan, extract, "", progress, model="Fake LLM", shuffle=False)

    assert len(tried) == 1                              # it stopped rather than trying the rest
    assert progress.ticks == []                         # and counted nothing as failed
    assert "60 minutes" in message and "run Parse again" in message
    assert load_extractions(ingest_dir)["1:1"].name != ""      # every earlier extraction is untouched


def test_what_each_call_took_is_kept_beside_its_result_and_in_the_log(ingest_dir):
    from data import EXTRACTION_RUNS, OCR_RUNS, load_model_runs
    import run_log

    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=0)
    plan.documents = [d for d in plan.documents if str(d) == "1:1"]

    def extract(ocr_text, has_boxes, custom_instruction, on_usage=None):
        on_usage(TokenUse(prompt=2000, cached=1024, completion=400, thinking=250))
        return _receipt("Priced")

    ip.run_parse(ingest_dir, plan, extract, "", FakeProgress(), model="OpenAI - gpt-6-luna", shuffle=False)

    [run] = load_model_runs(ingest_dir, EXTRACTION_RUNS).values()
    assert run.model == "OpenAI - gpt-6-luna" and run.seconds >= 0 and run.at > 0
    assert run.tokens.cached == 1024
    assert run.cost == pytest.approx(0.000308, abs=1e-5)
    assert load_model_runs(ingest_dir, OCR_RUNS) == {}          # this run read nothing

    [row] = run_log.read(ingest_dir)
    assert (row["kind"], row["job_id"], row["item"]) == ("parse", "test-job", "1:1")
    assert row["cost"] == pytest.approx(0.000308, abs=1e-5)


def test_a_local_model_records_its_time_and_no_money(ingest_dir, tmp_path):
    from data import OCR_RUNS, load_model_runs

    page = tmp_path / "page.png"
    page.write_bytes(b"not really a png")
    ip.run_ocr(ingest_dir, [("9:1", page)], FakeOcr(), False, FakeProgress(), model="DeepSeek OCR 2")

    [run] = load_model_runs(ingest_dir, OCR_RUNS).values()
    assert run.model == "DeepSeek OCR 2" and run.seconds >= 0
    assert run.tokens is None and run.cost is None


def test_run_parse_does_not_undo_what_changed_the_file_while_it_ran(ingest_dir):
    """Regrouping another batch clears its extractions mid-Parse; the next save must not bring them back."""
    from data import clear_extractions_decisions_for_batch, save_extractions

    extractions = load_extractions(ingest_dir)
    extractions["2:1"] = _receipt("Batch two, about to be regrouped")
    save_extractions(ingest_dir, extractions)
    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=2)
    calls = []

    def extract(ocr_text, has_boxes, custom_instruction):
        calls.append(1)
        if len(calls) == 1:
            clear_extractions_decisions_for_batch(ingest_dir, 2)   # File Index, on another batch, meanwhile
        return _receipt("Parsed")

    ip.run_parse(ingest_dir, plan, extract, "", FakeProgress(), model="Fake LLM", shuffle=False, save_every=0.0)
    after = load_extractions(ingest_dir)
    assert "2:1" not in after                                        # the regroup stuck
    assert sum(getattr(e, "name", None) == "Parsed" for e in after.values()) == 2   # and Parse's own results landed


def test_run_parse_saves_periodically(ingest_dir, monkeypatch):
    saves = []
    monkeypatch.setattr(ip, "merge_extractions", lambda path, produced: saves.append(len(produced)))
    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=3)
    now = iter([0.0, 5.0, 20.0, 20.0, 40.0, 40.0])  # start, item 1, item 2 (+save), item 3 (+save)
    ip.run_parse(ingest_dir, plan, lambda *a, **k: _receipt(), "", FakeProgress(), model="Fake LLM", shuffle=False,
                 save_every=15.0, clock=lambda: next(now))
    assert len(saves) == 3  # after item 2, after item 3, and the final save


# --- Archive -------------------------------------------------------------------------------------------------
def test_swapping_a_group_s_pages_is_saved_and_shown_in_that_order(ingest_dir):
    assert ip.save_grouping(ingest_dir, 1, [["1:1"], ["1:2"], ["1:3"], ["1:5", "1:4"], ["1:7"]]) is True
    assert load_document_groups(ingest_dir).groups == [["1:5", "1:4"]]
    state = ip.grouping_state(ingest_dir, 1)
    assert state.saved_groups == [["1:5", "1:4"]]
    assert state.display_keys.index("1:5") < state.display_keys.index("1:4")


def test_archive_files_a_document_s_pages_in_their_grouped_order(ingest_dir, tmp_path):
    from data import read_sidecar, save_document_groups
    from models import DocumentGroups

    save_document_groups(ingest_dir, DocumentGroups(groups=[["1:5", "1:4"]]))   # 1:5 is page 1
    ip.run_archive(ingest_dir, _scans(tmp_path, ingest_dir), FakeProgress())

    [first] = (ingest_dir / "2025" / "01").glob("*Tealive KLCC.png")
    [second] = (ingest_dir / "2025" / "01").glob("*Tealive KLCC (2).png")
    assert (read_sidecar(first).serial, read_sidecar(first).page) == (5, 1)
    assert (read_sidecar(second).serial, read_sidecar(second).page) == (4, 2)
    assert read_sidecar(first).document_key == "1:4-5"


def test_plan_archive_blocks_until_every_page_is_reviewed(ingest_dir):
    decisions = load_decisions(ingest_dir)
    del decisions["1:3"]
    save_decisions(ingest_dir, decisions)
    plan = ip.plan_archive(ingest_dir)
    assert plan.blocker == "Review all files before archiving." and plan.moves == []


def test_plan_archive_destinations(ingest_dir):
    plan = ip.plan_archive(ingest_dir)
    assert plan.blocker is None
    assert (plan.documents, plan.multipage, plan.files, plan.accepted, plan.tossed) == (7, 1, 8, 6, 2)
    destinations = {m.key: m.destination for m in plan.moves}
    assert destinations["1:6"] == "tossed/01102025142000_6.png"
    assert destinations["1:5"].endswith("Tealive KLCC (2).png")  # pages of one document get distinct names


def test_run_archive_copies_finalizes_and_cleans_up(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    progress = FakeProgress()
    message = ip.run_archive(ingest_dir, scans, progress)

    assert message.startswith("Archived 8 file(s).")
    assert progress.total == 8 and all(ok for ok, _, _ in progress.ticks)
    sidecar = json.loads((ingest_dir / "tossed" / "01102025142000_6.json").read_text(encoding="utf-8"))
    assert sidecar["review"]["verdict"] == "tossed" and sidecar["batch_id"] == 1
    multipage = json.loads(next((ingest_dir / "2025" / "01").glob("*Tealive KLCC (2).json")).read_text(encoding="utf-8"))
    assert multipage["document_key"] == "1:4-5"
    assert (scans / "01102025132642_1.png").exists()             # originals are copied, not moved
    assert load_scan_index(ingest_dir).batches[0].archived
    assert not any((ingest_dir / name).exists() for name in ip.CLEANUP_ARTIFACTS)
    assert "ocr" in ip.CLEANUP_ARTIFACTS and "ocr.json.migrated" in ip.CLEANUP_ARTIFACTS
    assert load_smart_match_cache(ingest_dir)["1:7"]["confirmed"] == "Business Card - John Doe"


def test_archiving_files_what_the_calls_behind_a_document_took(ingest_dir, tmp_path):
    """The mid-ingest records are cleaned up with the rest, so they travel into the sidecars first."""
    from data import EXTRACTION_RUNS, OCR_RUNS, merge_model_runs, read_sidecar
    from models import ModelRun

    merge_model_runs(ingest_dir, OCR_RUNS, {"1:1": ModelRun(model="DeepSeek OCR 2", at=1.0, seconds=31.5)})
    merge_model_runs(ingest_dir, EXTRACTION_RUNS, {"1:1": ModelRun(
        model="OpenAI - gpt-6-luna", at=2.0, seconds=1.25,
        tokens=TokenUse(prompt=2000, cached=1024, completion=400, thinking=250), cost=0.000308)})

    ip.run_archive(ingest_dir, _scans(tmp_path, ingest_dir), FakeProgress())

    first_page = next(p for p in (ingest_dir / "2025" / "01").glob("*.png") if "13：26" in p.name)   # key 1:1
    sidecar = read_sidecar(first_page)
    assert sidecar.ocr_run.model == "DeepSeek OCR 2" and sidecar.ocr_run.seconds == 31.5
    assert sidecar.ocr_run.cost is None                       # it read on this machine
    assert sidecar.extraction_run.cost == pytest.approx(0.000308)
    assert sidecar.extraction_run.tokens.cached == 1024


def test_run_archive_does_not_finalize_when_a_file_fails_and_resumes_later(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    (scans / "01102025133000_2.png").unlink()

    with pytest.raises(ip.ArchiveIncomplete):
        ip.run_archive(ingest_dir, scans, FakeProgress())
    assert not load_scan_index(ingest_dir).batches[0].archived
    assert (ingest_dir / "decisions.json").exists()

    Image.new("RGB", (40, 80)).save(scans / "01102025133000_2.png")
    progress = FakeProgress()
    assert ip.run_archive(ingest_dir, scans, progress).startswith("Archived 1 file(s).")  # only the one left
    assert load_scan_index(ingest_dir).batches[0].archived


def test_run_archive_redoes_a_file_whose_sidecar_failed(ingest_dir, tmp_path, monkeypatch):
    scans = _scans(tmp_path, ingest_dir)
    real_write_sidecar = ip.write_sidecar

    def flaky(target, sidecar):
        if target.parent.name == "tossed":
            raise OSError("disk full")
        real_write_sidecar(target, sidecar)

    monkeypatch.setattr(ip, "write_sidecar", flaky)
    with pytest.raises(ip.ArchiveIncomplete):
        ip.run_archive(ingest_dir, scans, FakeProgress())
    assert list((ingest_dir / "tossed").iterdir()) == []  # no image left that a re-run would skip

    monkeypatch.setattr(ip, "write_sidecar", real_write_sidecar)
    assert ip.run_archive(ingest_dir, scans, FakeProgress()).startswith("Archived 2 file(s).")
    assert sorted(p.name for p in (ingest_dir / "tossed").iterdir()) == [
        "01102025142000_6.json", "01102025142000_6.png", "01102025143000_8.json", "01102025143000_8.png"]


def test_run_archive_cancel_finalizes_nothing(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    message = ip.run_archive(ingest_dir, scans, FakeProgress(cancel_after=2))
    assert message.startswith("Cancelled after 2 of 8")
    assert not load_scan_index(ingest_dir).batches[0].archived


def test_a_run_where_everything_failed_does_not_read_as_success(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    items = [("1:1", scans / "01102025132642_1.png"), ("1:2", scans / "01102025133000_2.png")]

    class AlwaysFails:
        grounding = False

        def run(self, path, structured=False):
            raise RuntimeError("no credentials")

    message = ip.run_ocr(ingest_dir, items, AlwaysFails(), structured=False, progress=FakeProgress(),
                         model="Fake OCR", shuffle=False)
    assert message == "Every one of the 2 page(s) failed. Nothing usable was written."


def test_a_partly_failed_run_names_the_failures(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    items = [("1:1", scans / "01102025132642_1.png"), ("1:2", scans / "01102025133000_2.png")]
    provider = FakeOcr(fail_on="01102025133000_2.png")

    message = ip.run_ocr(ingest_dir, items, provider, structured=False, progress=FakeProgress(),
                         model="Fake OCR", shuffle=False)
    assert message == "OCR read 1 of 2 page(s). 1 failed."


# --- trims ---------------------------------------------------------------------------------------------
#: The fixture's trimmed page (trims.json), and the band OCR read it under (ocr/1.json).
TRIMMED, FIXTURE_TRIM = "1:3", (0.0, 0.75)


def test_the_fixture_s_trimmed_page_was_read_under_its_trim(ingest_dir, tmp_path):
    from data import load_trims
    from models import Trim

    band = Trim(top=FIXTURE_TRIM[0], bottom=FIXTURE_TRIM[1])
    assert load_trims(ingest_dir) == {TRIMMED: band}
    assert load_ocr_results(ingest_dir)[TRIMMED].trim == band
    plan = ip.plan_ocr(ingest_dir, _scans(tmp_path, ingest_dir), None, reprocess=False, limit=0)
    assert plan.items == [] and plan.retrimmed == 0            # read as it is trimmed: nothing is due


def test_a_page_trimmed_differently_after_it_was_read_is_read_again(ingest_dir, tmp_path):
    from data import load_trims
    from models import Trim

    scans = _scans(tmp_path, ingest_dir)
    ip.trim_page(ingest_dir, TRIMMED, Trim(top=0.0, bottom=0.6))          # the cut moves up
    ip.trim_page(ingest_dir, "1:2", Trim(top=0.1, bottom=1.0))             # a page read whole gets one

    plan = ip.plan_ocr(ingest_dir, scans, None, reprocess=False, limit=0)
    assert sorted(k for k, _ in plan.items) == ["1:2", TRIMMED] and plan.retrimmed == 2
    assert plan.processed == 8                            # they were read; they are due again, not unread
    ip.trim_page(ingest_dir, TRIMMED, Trim(top=FIXTURE_TRIM[0], bottom=FIXTURE_TRIM[1]))   # moved back
    ip.trim_page(ingest_dir, "1:2", None)                                                   # taken off
    assert ip.plan_ocr(ingest_dir, scans, None, reprocess=False, limit=0).items == []
    assert set(load_trims(ingest_dir)) == {TRIMMED}
    with pytest.raises(KeyError):
        ip.trim_page(ingest_dir, "1:99", Trim(top=0.1, bottom=1.0))


def test_ocr_of_the_coupon_scan_never_sees_the_coupon(ingest_dir, tmp_path):
    """The trims fixture: a receipt with a coupon under it, and the trim that keeps the receipt."""
    from models import Trim

    spec = json.loads((FIXTURES / "trims" / "receipt_with_coupon.json").read_text(encoding="utf-8"))
    scans = _scans(tmp_path, ingest_dir)
    shutil.copy(FIXTURES / "trims" / "receipt_with_coupon.png", scans / "01102025132642_1.png")      # page 1:1
    band = Trim.model_validate(spec["trim"])
    ip.trim_page(ingest_dir, "1:1", band)
    seen = []

    class LookingOcr(FakeOcr):
        def run(self, path, structured=False):
            with Image.open(path) as image:
                seen.append((image.height, set(image.convert("L").getdata())))
            return super().run(path, structured)

    ip.run_ocr(ingest_dir, [("1:1", scans / "01102025132642_1.png")], LookingOcr(), structured=True,
               progress=FakeProgress(), model="Fake", shuffle=False)

    assert len(seen) == 2                                              # the text pass and the boxes pass
    for height, greys in seen:
        assert spec["receipt_rows"][1] <= height < spec["tear_row"]      # cut in the gap above the tear line
        assert spec["receipt_mark"] in greys and spec["coupon_mark"] not in greys
    assert load_ocr_results(ingest_dir)["1:1"].trim == band


def test_ocr_reads_only_the_band_and_remembers_which(ingest_dir, tmp_path):
    from models import Trim

    scans = _scans(tmp_path, ingest_dir)                  # 40 x 80 scans
    ip.trim_page(ingest_dir, "1:1", Trim(top=0.5, bottom=1.0))
    seen = []

    class SizingOcr(FakeOcr):
        def run(self, path, structured=False):
            with Image.open(path) as image:
                seen.append(image.size)
            return super().run(path, structured)

    items = [("1:1", scans / "01102025132642_1.png"), ("1:2", scans / "01102025133000_2.png")]
    ip.run_ocr(ingest_dir, items, SizingOcr(), structured=True, progress=FakeProgress(), model="Fake", shuffle=False)

    assert seen == [(40, 40), (40, 40), (40, 80), (40, 80)]     # both passes on the band; the other page whole
    results = load_ocr_results(ingest_dir)
    assert results["1:1"].trim == Trim(top=0.5, bottom=1.0) and results["1:2"].trim is None
    assert ip.plan_ocr(ingest_dir, scans, None, reprocess=False, limit=0).retrimmed == 0


def test_a_trim_turns_with_its_page(ingest_dir, tmp_path):
    from data import load_trims
    from models import Trim

    scans = _scans(tmp_path, ingest_dir)
    ip.trim_page(ingest_dir, "1:1", Trim(top=0.1, bottom=0.6))

    ip.rotate_page_image(ingest_dir, scans, "1:1", "down")
    ip.rotate_page_image(ingest_dir, scans, TRIMMED, "left")

    assert load_trims(ingest_dir) == {"1:1": Trim(top=0.4, bottom=0.9)}   # a quarter turn leaves 1:3 whole


def test_the_grouping_editor_sees_each_page_s_trim(ingest_dir):
    from models import Trim

    pages = {page.key: page.trim for page in ip.grouping_state(ingest_dir, 1).pages}
    assert pages[TRIMMED] == Trim(top=FIXTURE_TRIM[0], bottom=FIXTURE_TRIM[1])
    assert all(t is None for k, t in pages.items() if k != TRIMMED)


def test_archiving_files_each_page_s_trim_with_it(ingest_dir, tmp_path):
    from data import TRIMS, read_sidecar
    from models import Trim

    ip.run_archive(ingest_dir, _scans(tmp_path, ingest_dir), FakeProgress())

    filed = {read_sidecar(p).original_filename: read_sidecar(p) for p in (ingest_dir / "2025" / "01").glob("*.png")}
    band = Trim(top=FIXTURE_TRIM[0], bottom=FIXTURE_TRIM[1])
    assert filed["01102025140000_3.png"].trim == band and filed["01102025140000_3.png"].ocr.trim == band
    assert all(s.trim is None for name, s in filed.items() if name != "01102025140000_3.png")
    assert not (ingest_dir / TRIMS).exists()
