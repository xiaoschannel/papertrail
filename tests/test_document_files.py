"""Moving a document's pages: scan order, no overwrites, never an image without its sidecar."""

from pathlib import Path

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
    real_rename = df.os.rename

    def failing(src, dst):
        if str(dst) == str(target):
            raise OSError("disk hiccup")
        return real_rename(src, dst)

    monkeypatch.setattr(df.os, "rename", failing)

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


# --- a document moves whole or not at all ---------------------------------------------------------------
def _snapshot(folder):
    return sorted((f.name, f.read_bytes()) for f in folder.rglob("*") if f.is_file())


def test_a_page_failing_part_way_puts_the_pages_already_moved_back(archive_dir, monkeypatch):
    pages, row = _document(archive_dir, multi=True)
    before = _snapshot(archive_dir)
    real_rename = df.os.rename
    moves = []

    def second_page_fails(src, dst):
        if Path(src).suffix != ".json":
            moves.append(src)
            if len(moves) == 2:
                raise OSError("disk hiccup")
        return real_rename(src, dst)

    monkeypatch.setattr(df.os, "rename", second_page_fails)
    with pytest.raises(OSError):
        df.place_document(archive_dir, df.load_pages(pages), _decision(row, name="Half Done"))

    assert _snapshot(archive_dir) == before          # every page back where it was, sidecars as they were


def test_a_file_open_elsewhere_stops_the_move_before_anything_moves(archive_dir, monkeypatch):
    pages, row = _document(archive_dir, multi=True)
    before = _snapshot(archive_dir)
    locked = pages[1]
    monkeypatch.setattr(df, "in_use", lambda path: str(path) == str(locked))

    with pytest.raises(df.FileInUse, match=__import__("re").escape(locked.name)):
        df.place_document(archive_dir, df.load_pages(pages), _decision(row, name="Never Moved"))
    with pytest.raises(df.FileInUse):
        df.toss_document(archive_dir, pages)

    assert _snapshot(archive_dir) == before


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows refuses to rename an open file")
def test_a_scan_really_open_in_another_program_is_refused(archive_dir):
    pages, row = _document(archive_dir, multi=True)
    assert not df.in_use(pages[1])
    with open(pages[1], "rb"):                        # a viewer holding the second page
        assert df.in_use(pages[1])
        with pytest.raises(df.FileInUse):
            df.place_document(archive_dir, df.load_pages(pages), _decision(row, name="Never Moved"))
    assert not df.in_use(pages[1])
    assert all(page.exists() and read_sidecar(page) is not None for page in pages)


def test_a_rename_that_only_changes_letter_case_moves_image_and_sidecar_together(archive_dir):
    # A single-page document whose name has letter case to change (not every script has one), whatever
    # order the file system lists the archive in.
    row = next(r for _, r in build_viz_records(archive_dir).iterrows()
               if len(r["paths"]) == 1 and r["name"].upper() != r["name"].lower())
    pages = [archive_dir / p for p in row["paths"]]
    renamed = row["name"].upper() if row["name"].upper() != row["name"] else row["name"].lower()

    decision = _decision(row, name=renamed)
    [placed] = df.place_document(archive_dir, df.load_pages(pages), decision,
                                 sidecar_for=lambda sidecar: sidecar.model_copy(update={"review": decision}))

    image = archive_dir / placed
    names = {f.name for f in image.parent.iterdir()}
    assert image.name in names and f"{image.stem}.json" in names   # the listed names carry the new case
    assert read_sidecar(image).review.name == renamed
    assert not any(n.startswith(pages[0].stem) for n in names if pages[0].stem != image.stem)


def test_names_in_use_are_compared_without_case(tmp_path):
    (tmp_path / "Shop.png").write_bytes(b"x")
    taken = df.taken_stems(tmp_path, set())
    assert df.free_name(tmp_path, "SHOP", ".png", taken).name == "SHOP (2).png"


# --- numbers under one name run without gaps -----------------------------------------------------------
def _same_minute_documents(archive_dir, count):
    """``count`` single-page documents filed under one name (as duplicates of one shop at one minute are)."""
    pages, row = _document(archive_dir)
    decision = _decision(row, name="Same Minute")
    placed = [df.place_document(archive_dir, df.load_pages(pages), decision,
                                sidecar_for=lambda s: s.model_copy(update={"review": decision}))[0]]
    first = archive_dir / placed[0]
    for n in range(2, count + 1):
        copy = first.with_name(f"copy{n}{first.suffix}")
        copy.write_bytes(first.read_bytes())
        sidecar = read_sidecar(first).model_copy(update={"original_filename": f"copy{n}.png", "serial": 900 + n})
        from data import write_sidecar
        write_sidecar(copy, sidecar)
        placed += df.place_document(archive_dir, df.load_pages([copy]), decision,
                                    sidecar_for=lambda s: s.model_copy(update={"review": decision}))
    return [archive_dir / p for p in placed]


def test_tossing_the_middle_one_renumbers_the_rest(archive_dir):
    first, second, third = _same_minute_documents(archive_dir, 3)
    assert [p.stem.endswith(s) for p, s in ((second, "(2)"), (third, "(3)"))] == [True, True]
    third_original = read_sidecar(third).original_filename

    df.toss_document(archive_dir, [second])

    names = {p.name for p in first.parent.iterdir() if "Same Minute" in p.name}
    assert names == {first.name, first.with_suffix(".json").name, second.name, second.with_suffix(".json").name}
    assert read_sidecar(second).original_filename == third_original       # the third moved down into (2)


def test_moving_the_first_one_away_gives_the_next_the_plain_name(archive_dir):
    first, second = _same_minute_documents(archive_dir, 2)
    second_original = read_sidecar(second).original_filename
    sidecar = read_sidecar(first)
    decision = sidecar.review.model_copy(update={"name": "Somewhere Else"})

    df.place_document(archive_dir, df.load_pages([first]), decision,
                      sidecar_for=lambda s: s.model_copy(update={"review": decision}))

    assert read_sidecar(first).original_filename == second_original       # (2) became the plain name
    assert not second.exists()

