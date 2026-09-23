"""The sandbox's shared state (tools/sandbox_seed.py): built through the real pipeline, every stage as it says."""

import pytest

import ingest_pipeline as pipeline
from data import load_decisions, load_extractions, load_trims, read_sidecar
from models import Trim, load_scan_index
from tools import sandbox_seed as seed

COUPON = Trim(top=seed.COUPON_TRIM[0], bottom=seed.COUPON_TRIM[1])


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    sb = seed.Sandbox(root=tmp_path_factory.mktemp("sandbox"))
    seed.build(sb)
    return sb


def test_the_first_batch_is_filed_with_some_of_it_marked(sandbox):
    first, second, _ = load_scan_index(sandbox.archive).batches
    assert first.archived and not second.archived
    marked = {read_sidecar(p).original_filename: read_sidecar(p)
              for p in (sandbox.archive / "marked").glob("*.png")}
    assert {name.split("_")[1] for name in marked} == {f"{serial}.png" for serial in seed.MARKED}
    drugstore = marked["02152026090030_4.png"]           # marked untrimmed: read with its coupon
    assert drugstore.trim is None and drugstore.review.cost == 3000.0
    assert drugstore.review.comment == seed.MARKED[4]


def test_the_filed_batch_s_scans_left_the_scan_folder_for_the_archive_s_history(sandbox):
    import archive_history

    import subprocess

    first, *_ = load_scan_index(sandbox.archive).batches
    assert not any((sandbox.scans / name).exists() for name in first.files.values())
    log = subprocess.run(["git", "-C", str(sandbox.root), "log", "--format=%s"], check=True, capture_output=True,
                         text=True, encoding="utf-8").stdout.splitlines()
    milestones = ["File Index: batch 3 (18 scans)", "Parse with ", "OCR with ", "File Index: batch 2 (4 scans)",
                  "Archive: 6 scans cleared out of the scan folder", "Archive batch 1: 6 files", "Parse with ",
                  "OCR with ", "File Index: batch 1 (6 scans)", "History started"]
    assert len(log) == len(milestones) and all(s.startswith(m) for s, m in zip(log, milestones)), log
    # trims aren't a milestone: batch 2's wait for one (or the Commit button)
    assert archive_history.changed_paths(sandbox.root) == {"archive/trims.json"}


def test_the_filed_coupon_receipt_is_trimmed_and_read_as_the_receipt_alone(sandbox):
    filed = [read_sidecar(p) for p in sandbox.archive.glob("2026/*/*.png")]
    [supermarket] = [s for s in filed if s.original_filename == "02152026090020_3.png"]
    assert supermarket.trim == COUPON and supermarket.ocr.trim == COUPON
    assert supermarket.review.verdict == "accepted" and supermarket.review.cost == 2150.0
    assert "COUPON" not in supermarket.ocr.markdown


def test_the_second_batch_waits_in_review(sandbox):
    extractions = load_extractions(sandbox.archive)
    assert sorted(extractions) == ["2:1", "2:2", "2:3", "2:4"]
    assert load_decisions(sandbox.archive) == {}                 # nothing decided: all of it is Review's
    assert extractions["2:1"].cost == 2380.0                     # trimmed before OCR: the receipt's own total
    assert extractions["2:2"].cost == 3000.0                     # trimmed after: still what was read, coupon and all
    assert load_trims(sandbox.archive) == {"2:1": COUPON, "2:2": COUPON}
    plan = pipeline.plan_ocr(sandbox.archive, sandbox.scans, 2, reprocess=False, limit=0)
    assert [key for key, _ in plan.items] == ["2:2"] and plan.retrimmed == 1


def test_the_third_batch_is_indexed_and_not_read_yet(sandbox):
    *_, batch = load_scan_index(sandbox.archive).batches
    assert batch.batch_id == 3 and not batch.archived and len(batch.files) == 18
    assert batch.files[18] == "03012026100218_18.png"            # the coupon receipt, to trim in Group
    assert not any(key.startswith("3:") for key in load_extractions(sandbox.archive))
    plan = pipeline.plan_ocr(sandbox.archive, sandbox.scans, 3, reprocess=False, limit=0)
    assert len(plan.items) == 18                                   # every page still to read


def test_no_scan_is_left_unindexed(sandbox):
    proposal = pipeline.propose_index(sandbox.scans, sandbox.archive, seed.SCHEME)
    assert proposal.unindexed_count == 0 and proposal.batches == []


def test_restarting_reads_every_scan_without_redrawing_it(sandbox):
    again = seed.Sandbox(root=sandbox.root)
    before = {p.name: p.stat().st_mtime_ns for p in sandbox.scans.glob("*.png")}
    seed.draw(again)
    assert again.text.keys() >= set(before)                       # the fake OCR can read every scan
    assert {p.name: p.stat().st_mtime_ns for p in sandbox.scans.glob("*.png")} == before
    assert "02152026090000_1.png" in again.text and not (sandbox.scans / "02152026090000_1.png").exists()   # filed: not back


def test_a_reset_puts_a_used_sandbox_back_into_the_shared_state(tmp_path):
    root = tmp_path / "sandbox"
    seed.prepare(root)
    (root / "scans" / "a scan dropped in.png").write_bytes(b"")          # what was done in it since
    (root / "archive" / "marked" / "02152026090030_4.png").unlink()

    sb = seed.prepare(root, fresh=True)

    assert not (sb.scans / "a scan dropped in.png").exists()
    assert (sb.archive / "marked" / "02152026090030_4.png").exists()
    assert [b.archived for b in load_scan_index(sb.archive).batches] == [True, False, False]


def test_a_sandbox_from_before_the_papertrail_folder_is_moved_onto_it(tmp_path, monkeypatch):
    import json

    import settings

    monkeypatch.setattr(settings, "CONFIG_PATH", settings.CONFIG_PATH)   # prepare points it at the sandbox
    root = tmp_path / "sandbox"
    seed.prepare(root)
    config = root / "config.json"
    old = {k: v for k, v in json.loads(config.read_text(encoding="utf-8")).items() if k != "root_path"}
    config.write_text(json.dumps({**old, "input_image_path": str(root / "scans"),
                                  "batch_output_path": str(root / "archive"), "ocr_model": "kept"}), encoding="utf-8")

    seed.prepare(root)

    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["root_path"] == str(root) and saved["ocr_model"] == "kept"
    assert "input_image_path" not in saved and "batch_output_path" not in saved
    assert settings.get_config().root_path == str(root)


def test_the_reset_script_leaves_a_running_sandbox_alone(monkeypatch, tmp_path):
    import socket
    import sys
    from types import SimpleNamespace

    from tools import sandbox_reset

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        monkeypatch.setattr(sandbox_reset, "checkout_ports", lambda repo: SimpleNamespace(sandbox_api=server.getsockname()[1]))
        # the module the script imports; if it ever got that far, nothing real is wiped
        monkeypatch.setitem(sys.modules, "sandbox_seed",
                            SimpleNamespace(prepare=lambda *a, **k: pytest.fail("rebuilt under a running server")))
        assert sandbox_reset.main() == 1
