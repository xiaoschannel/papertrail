"""Editing an already-archived document: re-filing, sidecars and the smart-match cache."""

from pathlib import Path

import pytest

import document_files
from data import load_smart_match_cache, read_sidecar
from models import ReviewDecision
from organize_utils import build_accepted_name
from receipt_edit import NotEditable, ReceiptEdit, apply_receipt_edit, edit_error
from viz_records import build_viz_records


def _record(archive_dir, key=None):
    df = build_viz_records(archive_dir)
    row = df[df["filename"] == key].iloc[0] if key else df.iloc[0]
    return row


def _edit(row, **changes):
    values = dict(document_type=row["document_type"], name=row["name"], date=row["date"], time=row["time"],
                  cost=row["cost"], currency=row["currency"], address=row["address"], language=row["language"],
                  comment=row["comment"])
    values.update(changes)
    return ReceiptEdit(**values)


def test_saving_unchanged_values_keeps_the_same_file(archive_dir):
    row = _record(archive_dir)
    before = list(row["paths"])

    after = apply_receipt_edit(archive_dir, before, _edit(row))

    assert after == before  # not renamed to "… (2).png" on every save
    assert (archive_dir / after[0]).exists()


def test_editing_renames_the_file_and_rewrites_the_sidecar(archive_dir):
    row = _record(archive_dir)
    [path] = apply_receipt_edit(archive_dir, list(row["paths"]),
                               _edit(row, name="Renamed Shop", cost=99.0, comment="checked the total"))

    assert "Renamed Shop" in path and not (archive_dir / row["path"]).exists()
    sidecar = read_sidecar(archive_dir / path)
    assert sidecar.review.name == "Renamed Shop" and sidecar.review.cost == 99.0
    assert sidecar.review.comment == "checked the total"
    assert sidecar.review.verdict == "accepted"                      # the verdict is never changed
    assert sidecar.extraction.name == "Renamed Shop"                 # extraction follows the review
    assert sidecar.original_filename == read_sidecar(archive_dir / path).original_filename


def test_every_page_of_a_multi_page_document_is_refiled_in_scan_order(archive_dir):
    row = next(r for _, r in build_viz_records(archive_dir).iterrows() if len(r["paths"]) > 1)
    serials = [read_sidecar(archive_dir / p).serial for p in row["paths"]]

    after = apply_receipt_edit(archive_dir, list(row["paths"]), _edit(row, name="Two Page Doc"))

    assert len(after) == len(serials)
    assert all("Two Page Doc" in p for p in after)                   # every page is re-filed, not just the first
    assert after[1].endswith("(2).png") and after[0] != after[1]     # pages keep distinct names
    # the earlier scan keeps the plain name; "(2)" sorts before "." by filename, so this pins the order
    assert [read_sidecar(archive_dir / p).serial for p in after] == sorted(serials)
    assert all((archive_dir / p).exists() and read_sidecar(archive_dir / p) for p in after)
    assert {read_sidecar(archive_dir / p).document_key for p in after} == {row["filename"]}


def test_re_saving_a_multi_page_document_does_not_swap_its_pages(archive_dir):
    row = next(r for _, r in build_viz_records(archive_dir).iterrows() if len(r["paths"]) > 1)
    first = apply_receipt_edit(archive_dir, list(row["paths"]), _edit(row, name="Stable Doc"))
    again = apply_receipt_edit(archive_dir, first, _edit(row, name="Stable Doc"))

    assert again == first
    assert [read_sidecar(archive_dir / p).serial for p in again] == [read_sidecar(archive_dir / p).serial
                                                                    for p in first]


def test_an_edit_failing_part_way_leaves_the_document_as_it_was(archive_dir, monkeypatch):
    row = next(r for _, r in build_viz_records(archive_dir).iterrows() if len(r["paths"]) > 1)
    month = (archive_dir / row["path"]).parent
    before = sorted((f.name, f.read_bytes()) for f in month.iterdir())
    real_rename = document_files.os.rename
    moves = []

    def second_page_fails(src, dst):
        if Path(src).suffix != ".json":
            moves.append(src)
            if len(moves) == 2:            # the second page's image fails to move
                raise OSError("disk hiccup")
        return real_rename(src, dst)

    monkeypatch.setattr(document_files.os, "rename", second_page_fails)
    with pytest.raises(OSError):
        apply_receipt_edit(archive_dir, list(row["paths"]), _edit(row, name="Half Done"))

    # the whole document is back under its old name, both pages with their own sidecars
    assert sorted((f.name, f.read_bytes()) for f in month.iterdir()) == before


def test_a_scan_without_a_sidecar_is_not_overwritten(archive_dir):
    """A file the archive can't see (no sidecar) still occupies its name."""
    row = _record(archive_dir)
    edit = _edit(row, name="Orphan Name")
    decision = ReviewDecision(verdict="accepted", document_type=edit.document_type, name=edit.name,
                              date=edit.date, time=edit.time, cost=edit.cost or 0.0, currency=edit.currency)
    folder, base, _ = build_accepted_name(decision, read_sidecar(archive_dir / row["path"]).original_filename)
    blocker = archive_dir / folder / f"{base}.png"          # exactly where the edit wants to put the scan
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_bytes(b"not to be clobbered")

    [moved] = apply_receipt_edit(archive_dir, list(row["paths"]), edit)

    assert blocker.read_bytes() == b"not to be clobbered"   # untouched
    assert moved.endswith("(2).png")                        # the edit stepped around it
    assert read_sidecar(archive_dir / moved).review.name == "Orphan Name"


def test_edit_remembers_the_confirmed_name_for_smart_match(archive_dir):
    row = _record(archive_dir)
    apply_receipt_edit(archive_dir, list(row["paths"]), _edit(row, name="Confirmed Name"))

    entry = next(v for v in load_smart_match_cache(archive_dir).values() if v["confirmed"] == "Confirmed Name")
    assert entry["extracted"]                                        # what the model had read stays


@pytest.mark.parametrize("changes, message", [
    ({"cost": None, "document_type": "receipt"}, "Receipt requires: cost"),
    ({"currency": "", "document_type": "receipt"}, "Receipt requires: currency"),
    ({"date": "not-a-date"}, "Date must be"),
])
def test_invalid_values_are_refused_before_anything_moves(archive_dir, changes, message):
    row = _record(archive_dir)
    edit = _edit(row, **changes)
    assert message in (edit_error(edit) or "")
    with pytest.raises(ValueError):
        apply_receipt_edit(archive_dir, list(row["paths"]), edit)
    assert (archive_dir / row["path"]).exists()


def test_a_document_without_a_sidecar_cannot_be_edited(archive_dir):
    row = _record(archive_dir)
    (archive_dir / row["path"]).with_suffix(".json").unlink()
    with pytest.raises(NotEditable):
        apply_receipt_edit(archive_dir, list(row["paths"]), _edit(row, name="No Sidecar"))
