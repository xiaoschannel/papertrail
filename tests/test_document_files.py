"""Moving a document's pages: scan order, no overwrites, never an image without its sidecar."""

import pytest

import document_files as df
from data import read_sidecar
from models import ReviewDecision
from viz_records import build_viz_records


def _document(archive_dir, multi=False):
    """(page paths, first sidecar) of an archived document."""
    rows = build_viz_records(archive_dir)
    row = next(r for _, r in rows.iterrows() if (len(r["paths"]) > 1) == multi)
    return [archive_dir / p for p in row["paths"]], row


def _decision(row, **changes):
    values = dict(verdict="accepted", document_type=row["document_type"], name=row["name"], date=row["date"],
                  time=row["time"], cost=row["cost"], currency=row["currency"], comment=row["comment"])
    values.update(changes)
    return ReviewDecision(**values)


def test_place_document_files_pages_in_scan_order(archive_dir):
    pages, row = _document(archive_dir, multi=True)
    serials = sorted(read_sidecar(p).serial for p in pages)

    placed = df.place_document(archive_dir, df.load_pages(pages), _decision(row, name="Ordered Doc"))

    assert [read_sidecar(archive_dir / p).serial for p in placed] == serials
    assert placed[1].endswith("(2).png")        # the later scan takes the suffix
    assert all("Ordered Doc" in p for p in placed)


def test_a_move_never_overwrites_an_existing_file(archive_dir):
    pages, row = _document(archive_dir)
    target = pages[0].with_name("blocker.png")
    target.write_bytes(b"keep me")

    with pytest.raises(FileExistsError):
        df.move_page(pages[0], target, read_sidecar(pages[0]))

    assert target.read_bytes() == b"keep me" and pages[0].exists()


def test_a_failed_move_leaves_no_sidecar_behind(archive_dir, monkeypatch):
    pages, row = _document(archive_dir)
    target = pages[0].with_name("elsewhere.png")
    monkeypatch.setattr(df.shutil, "move", lambda *a: (_ for _ in ()).throw(OSError("disk hiccup")))

    with pytest.raises(OSError):
        df.move_page(pages[0], target, read_sidecar(pages[0]))

    assert not target.exists() and not target.with_suffix(".json").exists()
    assert pages[0].exists() and read_sidecar(pages[0]) is not None


def test_toss_document_moves_every_page_under_its_original_name(archive_dir):
    pages, _row = _document(archive_dir, multi=True)
    originals = [read_sidecar(p).original_filename for p in pages]

    tossed = df.toss_document(archive_dir, pages)

    assert [p.split("/")[-1] for p in tossed] == originals
    assert all(p.startswith("tossed/") for p in tossed)
    assert all(not page.exists() for page in pages)                       # moved, not copied
    assert {read_sidecar(archive_dir / p).review.verdict for p in tossed} == {"tossed"}
    assert all(read_sidecar(archive_dir / p) is not None for p in tossed)  # sidecars came along


def test_tossing_two_documents_that_share_an_original_filename(archive_dir):
    pages, _row = _document(archive_dir)
    clash = archive_dir / "tossed" / read_sidecar(pages[0]).original_filename
    clash.parent.mkdir(parents=True, exist_ok=True)
    clash.write_bytes(b"an earlier scan with the same name")

    [tossed] = df.toss_document(archive_dir, pages)

    assert clash.read_bytes() == b"an earlier scan with the same name"    # the first one is untouched
    assert tossed.endswith("(2).png")


def test_a_page_without_a_sidecar_cannot_be_moved(archive_dir):
    pages, _row = _document(archive_dir)
    pages[0].with_suffix(".json").unlink()
    with pytest.raises(LookupError):
        df.load_pages(pages)
