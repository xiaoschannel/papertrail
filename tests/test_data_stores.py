"""Persistence round-trips and archive-state derivations in data.py."""

import json

import data


def _dump(d):
    return {k: v.model_dump() for k, v in d.items()}


def test_ocr_roundtrip(ingest_dir, tmp_path):
    ocr = data.load_ocr_results(ingest_dir)
    out = tmp_path / "rt"
    out.mkdir()
    data.save_ocr_results(out, ocr)
    assert _dump(data.load_ocr_results(out)) == _dump(ocr)


def test_extractions_roundtrip(ingest_dir, tmp_path):
    ext = data.load_extractions(ingest_dir)
    out = tmp_path / "rt"
    out.mkdir()
    data.save_extractions(out, ext)
    assert _dump(data.load_extractions(out)) == _dump(ext)


def test_decisions_roundtrip(ingest_dir, tmp_path):
    dec = data.load_decisions(ingest_dir)
    out = tmp_path / "rt"
    out.mkdir()
    data.save_decisions(out, dec)
    assert _dump(data.load_decisions(out)) == _dump(dec)


def test_distinct_pairs_roundtrip(archive_dir, tmp_path):
    pairs = data.load_distinct_pairs(archive_dir)
    out = tmp_path / "rt"
    out.mkdir()
    data.save_distinct_pairs(out, pairs)
    assert data.load_distinct_pairs(out) == pairs


def test_build_smart_match_history(ingest_dir):
    ext = data.load_extractions(ingest_dir)
    dec = data.load_decisions(ingest_dir)
    smart = data.load_smart_match_cache(ingest_dir)  # no file -> {}
    rows = data.build_smart_match_history(ext, dec, smart)
    # 4 accepted receipts + 1 accepted 'other'; tossed docs excluded.
    assert len(rows) == 5
    confirmed = {r.confirmed for r in rows}
    assert "セブン-イレブン 品川駅前店" in confirmed
    # the 'other' doc contributes its title as the extracted name
    assert any(r.extracted == "Business Card - John Doe" for r in rows)


def test_clear_extractions_decisions_keeps_only_tossed_without_extraction(tmp_path):
    # Characterizes the actual rule: the batch's extractions are all dropped, and
    # decisions are dropped for every key that is either non-tossed OR still has an
    # extraction. A tossed decision survives only when it has no extraction entry.
    from models import ReceiptResult, ReviewDecision

    def receipt(name, cost):
        return ReceiptResult(document_type="receipt", language="ja", date="2025-01-10",
                             time="10:00", name=name, currency="JPY", address="", cost=cost)

    def decision(verdict, name):
        return ReviewDecision(verdict=verdict, document_type="receipt", name=name,
                              date="2025-01-10", time="10:00", cost=1.0, currency="JPY")

    data.save_extractions(tmp_path, {"1:1": receipt("A", 1.0), "1:2": receipt("B", 2.0)})
    data.save_decisions(tmp_path, {
        "1:1": decision("accepted", "A"),
        "1:2": decision("tossed", "B"),    # tossed BUT has an extraction -> removed
        "1:9": decision("tossed", "C"),    # tossed, no extraction -> preserved
    })

    data.clear_extractions_decisions_for_batch(tmp_path, 1)

    assert data.load_extractions(tmp_path) == {}
    assert set(data.load_decisions(tmp_path)) == {"1:9"}


def test_replace_groups_for_batch(ingest_dir):
    data.replace_groups_for_batch(ingest_dir, 1, [["1:1", "1:2"], ["1:3"]])
    groups = data.load_document_groups(ingest_dir).groups
    # only multi-page groups are persisted; the old [1:4,1:5] group is replaced
    assert groups == [["1:1", "1:2"]]


def test_load_reorganized_state(archive_dir):
    tossed, accepted = data.load_reorganized_state(archive_dir)
    assert tossed == {"08102025143000_201.png"}
    assert len(accepted) == 9
    sidecar, rel = accepted["01102025132642_101.png"]
    assert sidecar.review.name == "セブン-イレブン 品川駅前店"
    assert rel == "2025/01/2025年1月10日 13：26 セブン-イレブン 品川駅前店.png"


def test_legacy_ocr_superset_sidecar_loads(archive_dir):
    # Older archived sidecars store OCR with legacy `filename`/`raw` keys
    # alongside the current `markdown`/`succeeded`. The loader must tolerate them.
    from models import Sidecar

    path = archive_dir / "2025/01/2025年1月10日 13：26 セブン-イレブン 品川駅前店.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert {"filename", "raw"} <= set(on_disk["ocr"])  # legacy extras present on disk

    sidecar = Sidecar.model_validate_json(path.read_text(encoding="utf-8"))
    assert sidecar.ocr.markdown.startswith("セブン-イレブン 品川駅前店")
    assert sidecar.ocr.succeeded is True
    assert not hasattr(sidecar.ocr, "raw")  # extras are dropped, not retained


def test_scan_organized_filenames(archive_dir):
    organized = data.scan_organized_filenames(archive_dir)
    # 9 archived sidecars + 1 tossed + 1 marked image
    assert len(organized) == 11
    assert "08102025143000_201.png" in organized  # tossed
    assert "08102025142000_202.png" in organized  # marked


def test_save_decisions_retries_while_the_file_is_briefly_locked(ingest_dir, monkeypatch):
    # Windows refuses to replace a file another process has open; the save waits it out.
    from pathlib import Path

    import data

    decisions = data.load_decisions(ingest_dir)
    real_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(data.time, "sleep", lambda _s: None)
    decisions["1:6"] = decisions["1:6"].model_copy(update={"comment": "saved"})
    data.save_decisions(ingest_dir, decisions)

    assert calls["n"] == 3
    assert data.load_decisions(ingest_dir)["1:6"].comment == "saved"
    assert not list(ingest_dir.glob("decisions.*.tmp"))


def test_save_decisions_gives_up_and_cleans_up_when_locked_for_good(ingest_dir, monkeypatch):
    from pathlib import Path

    import pytest

    import data

    before = (ingest_dir / "decisions.json").read_text(encoding="utf-8")
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError("locked")))
    monkeypatch.setattr(data.time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        data.save_decisions(ingest_dir, {})
    assert (ingest_dir / "decisions.json").read_text(encoding="utf-8") == before
    assert not list(ingest_dir.glob("decisions.*.tmp"))
