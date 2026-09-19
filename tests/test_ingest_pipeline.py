"""Ingest pipeline steps against the mid-ingest fixture, with fake OCR/extraction models."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

import ingest_pipeline as ip
from data import (
    load_decisions, load_document_groups, load_extractions, load_ocr_results, load_smart_match_cache, save_decisions,
    save_ocr_results,
)
from models import OcrResult, ReceiptResult, iter_indexed_files, load_scan_index

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeProgress:
    def __init__(self, cancel_after: int | None = None):
        self.total = None
        self.ticks: list[tuple[bool, str, str]] = []
        self.cancel_after = cancel_after

    @property
    def cancelled(self) -> bool:
        return self.cancel_after is not None and len(self.ticks) >= self.cancel_after

    def set_total(self, total):
        self.total = total

    def tick(self, ok=True, item="", error=""):
        self.ticks.append((ok, item, error))

    def say(self, message):
        pass


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

    ip.run_ocr(ingest_dir, items, provider, structured=True, progress=progress, shuffle=False)

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
    ip.run_ocr(ingest_dir, items, provider, structured=False, progress=progress, shuffle=False)
    assert provider.calls == [("01102025132642_1.png", False)]


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
    ip.run_parse(ingest_dir, plan, extract, "Prefer Japanese names", progress, shuffle=False)
    extractions = load_extractions(ingest_dir)
    assert extractions["1:1"].name == "Edited"
    assert extractions["1:3"].name == "上海小笼包馆"            # failure keeps the old extraction
    assert "1:6" in extractions and "1:8" in extractions        # tossed documents' extractions survive
    assert sorted(progress.ticks) == [(False, "1:3", "ValueError: model returned junk"), (True, "1:1", "")]


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

    ip.run_parse(ingest_dir, plan, extract, "", FakeProgress(), shuffle=False, save_every=0.0)
    after = load_extractions(ingest_dir)
    assert "2:1" not in after                                        # the regroup stuck
    assert sum(getattr(e, "name", None) == "Parsed" for e in after.values()) == 2   # and Parse's own results landed


def test_run_parse_saves_periodically(ingest_dir, monkeypatch):
    saves = []
    monkeypatch.setattr(ip, "merge_extractions", lambda path, produced: saves.append(len(produced)))
    plan = ip.plan_parse(ingest_dir, reprocess=True, limit=3)
    now = iter([0.0, 5.0, 20.0, 20.0, 40.0, 40.0])  # start, item 1, item 2 (+save), item 3 (+save)
    ip.run_parse(ingest_dir, plan, lambda *a, **k: _receipt(), "", FakeProgress(), shuffle=False,
                 save_every=15.0, clock=lambda: next(now))
    assert len(saves) == 3  # after item 2, after item 3, and the final save


# --- Archive -------------------------------------------------------------------------------------------------
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
    assert load_smart_match_cache(ingest_dir)["1:7"]["confirmed"] == "Business Card - John Doe"


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

    message = ip.run_ocr(ingest_dir, items, AlwaysFails(), structured=False, progress=FakeProgress(), shuffle=False)
    assert message == "Every one of the 2 page(s) failed. Nothing usable was written."


def test_a_partly_failed_run_names_the_failures(ingest_dir, tmp_path):
    scans = _scans(tmp_path, ingest_dir)
    items = [("1:1", scans / "01102025132642_1.png"), ("1:2", scans / "01102025133000_2.png")]
    provider = FakeOcr(fail_on="01102025133000_2.png")

    message = ip.run_ocr(ingest_dir, items, provider, structured=False, progress=FakeProgress(), shuffle=False)
    assert message == "OCR read 1 of 2 page(s). 1 failed."
