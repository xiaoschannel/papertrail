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
    first, second = load_scan_index(sandbox.archive).batches
    assert first.archived and not second.archived
    marked = {read_sidecar(p).original_filename: read_sidecar(p)
              for p in (sandbox.archive / "marked").glob("*.png")}
    assert {name.split("_")[1] for name in marked} == {f"{serial}.png" for serial in seed.MARKED}
    drugstore = marked["02152026090030_4.png"]           # marked untrimmed: read with its coupon
    assert drugstore.trim is None and drugstore.review.cost == 3000.0
    assert drugstore.review.comment == seed.MARKED[4]


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


def test_the_unindexed_scans_make_one_new_batch(sandbox):
    proposal = pipeline.propose_index(sandbox.scans, sandbox.archive, seed.SCHEME)
    assert proposal.error is None and len(proposal.batches) == 1
    [batch] = proposal.batches
    assert batch.batch_id == 3 and len(batch.files) == 18
    assert batch.files[18] == "03012026100218_18.png"            # the coupon receipt, to trim in Group


def test_restarting_reads_every_scan_without_redrawing_it(sandbox):
    again = seed.Sandbox(root=sandbox.root)
    before = {p.name: p.stat().st_mtime_ns for p in sandbox.scans.glob("*.png")}
    seed.draw(again)
    assert again.text.keys() >= set(before)                       # the fake OCR can read every scan
    assert {p.name: p.stat().st_mtime_ns for p in sandbox.scans.glob("*.png")} == before


def test_a_reset_puts_a_used_sandbox_back_into_the_shared_state(tmp_path):
    root = tmp_path / "sandbox"
    seed.prepare(root)
    (root / "scans" / "a scan dropped in.png").write_bytes(b"")          # what was done in it since
    (root / "archive" / "marked" / "02152026090030_4.png").unlink()

    sb = seed.prepare(root, fresh=True)

    assert not (sb.scans / "a scan dropped in.png").exists()
    assert (sb.archive / "marked" / "02152026090030_4.png").exists()
    assert [b.archived for b in load_scan_index(sb.archive).batches] == [True, False]


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
