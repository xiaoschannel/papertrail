"""Slicing a sheet of small receipts into crops: the grid, the plan, and applying it (slicing.py)."""

from pathlib import Path

import pytest
from PIL import Image

import slicing as sl
from data import (
    OCR_RUNS, load_decisions, load_extractions, load_model_runs, load_ocr_batch, merge_model_runs,
    save_decisions, save_ocr_batch,
)
from models import Box, ModelRun, OcrResult, ReviewDecision, SheetGrid, iter_indexed_files, load_scan_index


def grid(cells, rows=(), cols=(), frame=(0, 0, 1000, 1000)) -> SheetGrid:
    x1, y1, x2, y2 = frame
    return SheetGrid(frame=Box(x1=x1, y1=y1, x2=x2, y2=y2), row_lines=list(rows), col_lines=list(cols),
                     cells=cells)


#: 2 x 2 with the last cell empty: three receipts
THREE = grid([(1, 1), (1, 2), (2, 1)], rows=[500], cols=[500])
TWO = grid([(1, 1), (1, 2)], cols=[500])


@pytest.fixture
def scans(tmp_path, ingest_dir) -> Path:
    folder = tmp_path / "scans"
    folder.mkdir()
    for _, _, fn in iter_indexed_files(load_scan_index(ingest_dir)):
        Image.new("RGB", (100, 200), "white").save(folder / fn)
    return folder


def slice_(ingest_dir, scans, sheet, g):
    plan = sl.plan_slices(ingest_dir, 1, sheet, g)
    return sl.apply_slices(ingest_dir, scans, 1, sheet, g, plan.token)


def batch(ingest_dir):
    return load_scan_index(ingest_dir).batches[0]


def crops(ingest_dir) -> dict[int, tuple[int, int, int]]:
    return {s: (of.sheet, of.row, of.col) for s, of in batch(ingest_dir).slices.items()}


def give_results(ingest_dir, *serials):
    """OCR, a run, an extraction and a decision for each of these pages, as if they had been through ingest."""
    ocr = load_ocr_batch(ingest_dir, 1)
    extraction = next(iter(load_extractions(ingest_dir).values()))
    decisions = load_decisions(ingest_dir)
    from data import merge_extractions
    for serial in serials:
        key = f"1:{serial}"
        ocr[key] = OcrResult(markdown=f"ticket {serial}")
        merge_model_runs(ingest_dir, OCR_RUNS, {key: ModelRun(model="m", at=1.0, seconds=1.0)})
        merge_extractions(ingest_dir, {key: extraction})
        decisions[key] = ReviewDecision(verdict="marked", document_type="receipt", name="", date="", time="")
    save_ocr_batch(ingest_dir, 1, ocr)
    save_decisions(ingest_dir, decisions)


def has_results(ingest_dir, serial) -> bool:
    key = f"1:{serial}"
    return (key in load_ocr_batch(ingest_dir, 1) and key in load_model_runs(ingest_dir, OCR_RUNS)
            and key in load_extractions(ingest_dir) and key in load_decisions(ingest_dir))


# --- the grid -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    grid([(1, 1)], frame=(0, 0, 1000, 1000)),               # one cell over the whole sheet cuts nothing
    grid([], cols=[500]),                                    # no receipt
    grid([(1, 1), (1, 1)], cols=[500]),                     # a cell twice
    grid([(1, 3)], cols=[500]),                              # outside the grid
    grid([(1, 1)], cols=[600, 400]),                         # lines out of order
    grid([(1, 1)], cols=[1000]),                             # a line on the frame's edge
    grid([(1, 1)], frame=(0, 0, 1200, 1000)),               # off the sheet
    grid([(1, 1)], cols=list(range(50, 1000, 50))),         # more than 12 columns
])
def test_a_grid_that_cuts_nothing_real_is_refused(bad):
    with pytest.raises(sl.SliceRefused):
        sl.validate_grid(bad)


def test_a_one_by_one_grid_can_trim_the_margins():
    sl.validate_grid(grid([(1, 1)], frame=(50, 50, 950, 950)))


def test_cells_are_read_row_by_row_and_boxed_by_the_lines():
    g = grid([(2, 1), (1, 2), (1, 1)], rows=[400], cols=[300], frame=(100, 0, 900, 1000))
    assert sl.filled_cells(g) == [(1, 1), (1, 2), (2, 1)]
    assert sl.cell_box(g, 1, 2) == Box(x1=300, y1=0, x2=900, y2=400)
    assert sl.cell_box(g, 2, 1) == Box(x1=100, y1=400, x2=300, y2=1000)


def test_a_crop_is_its_cell_of_the_sheet(tmp_path):
    sheet = tmp_path / "sheet.png"
    img = Image.new("RGB", (100, 200), "white")
    img.paste((255, 0, 0), (50, 100, 100, 200))            # the bottom-right quarter is red
    img.save(sheet)
    sl.cut_crop(sheet, Box(x1=500, y1=500, x2=1000, y2=1000), tmp_path / "out" / "crop.png")
    with Image.open(tmp_path / "out" / "crop.png") as crop:
        assert crop.format == "PNG" and crop.size == (50, 100)
        assert crop.getpixel((25, 50)) == (255, 0, 0)


def test_a_jpeg_sheet_gives_jpeg_crops(tmp_path):
    sheet = tmp_path / "sheet.jpg"
    Image.new("RGB", (100, 100), "white").save(sheet, format="JPEG")
    sl.cut_crop(sheet, Box(x1=0, y1=0, x2=500, y2=1000), tmp_path / "crop.jpg")
    with Image.open(tmp_path / "crop.jpg") as crop:
        assert crop.format == "JPEG" and crop.size == (50, 100)
    assert not list(tmp_path.glob("*.cutting*"))


# --- slicing -------------------------------------------------------------------------------------------
def test_slicing_appends_crops_after_the_scans_and_tosses_the_sheet(ingest_dir, scans):
    plan = slice_(ingest_dir, scans, 2, THREE)

    assert plan.new_keys == ["1:9", "1:10", "1:11"] and plan.replaces_decision   # 1:2 was accepted
    b = batch(ingest_dir)
    assert sorted(b.files) == list(range(1, 12))
    assert crops(ingest_dir) == {9: (2, 1, 1), 10: (2, 1, 2), 11: (2, 2, 1)}
    assert b.files[10] == "slices/01102025133000_2.r1c2.500-0-1000-500.png"
    with Image.open(scans / b.files[10]) as crop:
        assert crop.size == (50, 100)
    assert load_decisions(ingest_dir)["1:2"].sliced
    assert "1:2" not in load_extractions(ingest_dir)            # what Parse made of the sheet goes
    assert "1:2" in load_ocr_batch(ingest_dir, 1)                # its OCR stays: the sheet is kept, tossed


def test_slicing_in_scan_order_never_moves_a_crop(ingest_dir, scans):
    slice_(ingest_dir, scans, 2, THREE)
    give_results(ingest_dir, 9, 10, 11)
    plan = slice_(ingest_dir, scans, 3, TWO)

    assert plan.moved == [] and plan.kept == {9, 10, 11} and (plan.ocr, plan.decisions) == (0, 0)
    assert crops(ingest_dir)[12] == (3, 1, 1) and crops(ingest_dir)[13] == (3, 1, 2)
    assert all(has_results(ingest_dir, s) for s in (9, 10, 11))


def test_slicing_an_earlier_sheet_moves_the_later_crops_and_drops_their_results(ingest_dir, scans):
    slice_(ingest_dir, scans, 3, TWO)                            # 1:9, 1:10
    give_results(ingest_dir, 9, 10)
    plan = sl.plan_slices(ingest_dir, 1, 2, THREE)

    assert plan.moved == [(9, 12), (10, 13)]
    assert (plan.ocr, plan.extractions, plan.decisions) == (2, 2, 2)
    sl.apply_slices(ingest_dir, scans, 1, 2, THREE, plan.token)
    assert crops(ingest_dir) == {9: (2, 1, 1), 10: (2, 1, 2), 11: (2, 2, 1), 12: (3, 1, 1), 13: (3, 1, 2)}
    assert not any(has_results(ingest_dir, s) for s in range(9, 14))
    assert load_decisions(ingest_dir)["1:3"].sliced and "1:1" in load_decisions(ingest_dir)   # others untouched


def test_emptying_a_cell_only_touches_the_crops_after_it(ingest_dir, scans):
    full = grid([(1, 1), (1, 2), (2, 1), (2, 2)], rows=[500], cols=[500])
    slice_(ingest_dir, scans, 2, full)                           # 1:9 .. 1:12
    give_results(ingest_dir, 9, 10, 11, 12)
    plan = slice_(ingest_dir, scans, 2, grid([(1, 1), (2, 1), (2, 2)], rows=[500], cols=[500]))

    assert plan.kept == {9} and plan.moved == [(11, 10), (12, 11)]
    assert has_results(ingest_dir, 9) and not any(has_results(ingest_dir, s) for s in (10, 11, 12))
    assert sorted(batch(ingest_dir).files) == list(range(1, 12))          # no gap where 1:12 was


def test_moving_a_line_recuts_only_the_cells_along_it(ingest_dir, scans):
    strip = grid([(1, 1), (1, 2), (1, 3)], cols=[300, 600])
    slice_(ingest_dir, scans, 2, strip)
    give_results(ingest_dir, 9, 10, 11)
    plan = slice_(ingest_dir, scans, 2, strip.model_copy(update={"col_lines": [400, 600]}))

    assert plan.kept == {11} and plan.moved == []
    assert has_results(ingest_dir, 11) and not has_results(ingest_dir, 9)
    with Image.open(scans / batch(ingest_dir).files[9]) as crop:
        assert crop.size == (40, 200)                           # re-cut to the new line


def test_saving_the_same_grid_changes_nothing(ingest_dir, scans):
    slice_(ingest_dir, scans, 2, THREE)
    give_results(ingest_dir, 9, 10, 11)
    plan = slice_(ingest_dir, scans, 2, THREE)
    assert plan.dropped_keys == set() and all(has_results(ingest_dir, s) for s in (9, 10, 11))


def test_unslicing_puts_the_sheet_back_and_closes_the_gap(ingest_dir, scans):
    slice_(ingest_dir, scans, 2, THREE)
    slice_(ingest_dir, scans, 3, TWO)                            # 1:12, 1:13
    plan = slice_(ingest_dir, scans, 2, None)

    assert plan.moved == [(12, 9), (13, 10)]
    assert crops(ingest_dir) == {9: (3, 1, 1), 10: (3, 1, 2)} and 2 not in batch(ingest_dir).grids
    assert "1:2" not in load_decisions(ingest_dir)
    assert sorted(p.name for p in (scans / "slices").iterdir()) == [
        "01102025140000_3.r1c1.0-0-500-1000.png", "01102025140000_3.r1c2.500-0-1000-1000.png"]   # 1:2's are swept


# --- what can't be sliced ----------------------------------------------------------------------------
def test_a_crop_a_tossed_page_or_a_linked_page_cant_be_sliced(ingest_dir, scans):
    slice_(ingest_dir, scans, 2, THREE)
    with pytest.raises(sl.SliceRefused, match="is a crop"):
        sl.plan_slices(ingest_dir, 1, 9, TWO)
    with pytest.raises(sl.SliceRefused, match="recover it"):
        sl.plan_slices(ingest_dir, 1, 6, TWO)                    # tossed by hand in the fixture
    with pytest.raises(sl.SliceRefused, match="multi-page"):
        sl.plan_slices(ingest_dir, 1, 4, TWO)                    # 1:4-5 is one document
    with pytest.raises(sl.SliceRefused, match="isn't sliced"):
        sl.plan_slices(ingest_dir, 1, 3, None)
    with pytest.raises(KeyError):
        sl.plan_slices(ingest_dir, 7, 1, TWO)


def test_a_stale_plan_is_refused(ingest_dir, scans):
    plan = sl.plan_slices(ingest_dir, 1, 2, THREE)
    slice_(ingest_dir, scans, 3, TWO)
    with pytest.raises(sl.StaleSlicePlan):
        sl.apply_slices(ingest_dir, scans, 1, 2, THREE, plan.token)


def test_a_batch_whose_crops_dont_match_its_grids_is_invalid(ingest_dir, scans):
    slice_(ingest_dir, scans, 2, THREE)
    good = batch(ingest_dir)
    sl.validate_slices(good)
    gap = good.model_copy(update={"files": {**{s: f for s, f in good.files.items() if s != 9}, 12: good.files[9]},
                                  "slices": {**{s: o for s, o in good.slices.items() if s != 9}, 12: good.slices[9]}})
    swapped = good.model_copy(update={"slices": {**good.slices, 9: good.slices[10], 10: good.slices[9]}})
    crop_as_sheet = good.model_copy(update={"grids": {**good.grids, 9: TWO}})
    for bad in (gap, swapped, crop_as_sheet):
        with pytest.raises(ValueError):
            sl.validate_slices(bad)


# --- interrupted applies ------------------------------------------------------------------------------
def test_a_crash_before_the_batch_is_written_is_reported_and_repaired(ingest_dir, scans, monkeypatch):
    plan = sl.plan_slices(ingest_dir, 1, 2, THREE)
    monkeypatch.setattr(sl, "save_scan_index", lambda *a: (_ for _ in ()).throw(OSError("power cut")))
    with pytest.raises(OSError):
        sl.apply_slices(ingest_dir, scans, 1, 2, THREE, plan.token)
    monkeypatch.undo()

    b = batch(ingest_dir)
    sl.validate_slices(b)                                        # batches.json is as it was
    assert sl.mismatches(b, load_decisions(ingest_dir)) == ["1:2 is tossed as sliced but has no grid"]
    assert (scans / "slices").is_dir()                           # crops were cut, unused

    slice_(ingest_dir, scans, 2, TWO)                            # saving again repairs it
    assert sl.mismatches(batch(ingest_dir), load_decisions(ingest_dir)) == []
    assert sorted(p.name for p in (scans / "slices").iterdir()) == [
        "01102025133000_2.r1c1.0-0-500-1000.png", "01102025133000_2.r1c2.500-0-1000-1000.png"]   # unused ones swept


def test_a_crash_while_recutting_leaves_every_crop_the_batch_names_as_it_says(ingest_dir, scans, monkeypatch):
    """Moving a line re-cuts the cells along it into files of their own; until the batch is written it still
    names the old ones, untouched."""
    strip = grid([(1, 1), (1, 2), (1, 3)], cols=[300, 600])
    slice_(ingest_dir, scans, 2, strip)
    before = {s: (scans / f).read_bytes() for s, f in batch(ingest_dir).files.items() if s in batch(ingest_dir).slices}
    moved = strip.model_copy(update={"col_lines": [400, 600]})
    plan = sl.plan_slices(ingest_dir, 1, 2, moved)
    monkeypatch.setattr(sl, "save_scan_index", lambda *a: (_ for _ in ()).throw(OSError("power cut")))
    with pytest.raises(OSError):
        sl.apply_slices(ingest_dir, scans, 1, 2, moved, plan.token)
    monkeypatch.undo()

    b = batch(ingest_dir)
    assert b.grids[2] == strip and sl.mismatches(b, load_decisions(ingest_dir)) == []
    for crop in sl.current_crops(b):
        assert crop.filename == sl.crop_filename(b.files[2], crop.of.row, crop.of.col, crop.box)
        assert (scans / crop.filename).read_bytes() == before[crop.serial]


def test_a_missing_sheet_fails_before_anything_changes(ingest_dir, scans):
    decisions = load_decisions(ingest_dir)
    (scans / "01102025133000_2.png").unlink()
    plan = sl.plan_slices(ingest_dir, 1, 2, THREE)
    with pytest.raises(FileNotFoundError):
        sl.apply_slices(ingest_dir, scans, 1, 2, THREE, plan.token)
    assert load_decisions(ingest_dir) == decisions and batch(ingest_dir).grids == {}


def test_crops_are_cut_from_the_sheet_as_a_browser_shows_it(tmp_path):
    """A JPEG whose EXIF says "turn me" is shown turned, so that is the sheet the grid was drawn on."""
    sheet = tmp_path / "sheet.jpg"
    img = Image.new("RGB", (200, 100), "white")
    img.paste((255, 0, 0), (0, 0, 100, 100))               # red on the left as stored
    exif = Image.Exif()
    exif[0x0112] = 6                                         # rotate 90 clockwise to display
    img.save(sheet, format="JPEG", exif=exif)
    sl.cut_crop(sheet, Box(x1=0, y1=0, x2=1000, y2=500), tmp_path / "top.jpg")
    with Image.open(tmp_path / "top.jpg") as crop:
        r, g, b = crop.getpixel((50, 50))
        assert crop.size == (100, 100) and r > 200 and g < 60   # the red half is on top once turned


def test_unslicing_repairs_a_sheet_tossed_as_sliced_without_a_grid(ingest_dir, scans):
    decisions = load_decisions(ingest_dir)
    decisions["1:2"] = sl.sliced_decision()
    save_decisions(ingest_dir, decisions)
    slice_(ingest_dir, scans, 2, None)
    assert "1:2" not in load_decisions(ingest_dir)


def test_a_grid_without_its_toss_is_reported(ingest_dir, scans):
    slice_(ingest_dir, scans, 2, THREE)
    decisions = load_decisions(ingest_dir)
    del decisions["1:2"]
    assert sl.mismatches(batch(ingest_dir), decisions) == ["1:2 is sliced but not tossed as sliced"]


def test_the_crops_are_pages_ocr_reads_like_any_other_and_the_sheet_is_not(ingest_dir, scans):
    import ingest_pipeline as ip

    slice_(ingest_dir, scans, 2, THREE)
    plan = ip.plan_ocr(ingest_dir, scans, None, reprocess=False, limit=0)
    assert plan.items == [(f"1:{s}", scans / "slices" / f"01102025133000_2.{cell}.png")
                          for s, cell in ((9, "r1c1.0-0-500-500"), (10, "r1c2.500-0-1000-500"),
                                          (11, "r2c1.0-500-500-1000"))]
    everything = ip.plan_ocr(ingest_dir, scans, None, reprocess=True, limit=0)
    assert "1:2" not in [k for k, _ in everything.items] and "1:6" in [k for k, _ in everything.items]


# --- the taped-tickets fixture ------------------------------------------------------------------------
def test_the_fixture_grid_cuts_every_ticket_out_whole_and_in_reading_order(ingest_dir, scans):
    """Tickets of uneven sizes with two empty cells: each crop holds exactly one whole ticket, the right one."""
    import json
    import shutil

    fixture = Path(__file__).resolve().parent / "fixtures" / "sheets"
    spec = json.loads((fixture / "taped_tickets.json").read_text(encoding="utf-8"))
    shutil.copy(fixture / "taped_tickets.png", scans / "01102025133000_2.png")      # sheet 1:2
    plan = slice_(ingest_dir, scans, 2, SheetGrid.model_validate(spec["grid"]))

    assert len(plan.new_keys) == len(spec["tickets"]) == 7
    marks = {spec["ticket_mark"] * (i + 1) for i in range(len(spec["tickets"]))}
    for number, crop in enumerate(sl.current_crops(batch(ingest_dir)), start=1):
        with Image.open(scans / crop.filename) as img:
            gray = img.convert("L")
            w, h = gray.size
            edge = {gray.getpixel((x, y)) for x in range(w) for y in (0, h - 1)} | \
                   {gray.getpixel((x, y)) for y in range(h) for x in (0, w - 1)}
            assert edge == {spec["background"]}, f"crop {number} cuts through a ticket"
            assert set(gray.tobytes()) & marks == {spec["ticket_mark"] * number}, f"crop {number} holds another ticket"


# --- archive ---------------------------------------------------------------------------------------------
def test_archive_files_crops_with_where_they_came_from_and_the_sheet_under_tossed(ingest_dir, scans):
    import ingest_pipeline as ip
    from data import read_sidecar

    slice_(ingest_dir, scans, 2, THREE)                          # 1:9, 1:10, 1:11
    decisions = load_decisions(ingest_dir)
    for serial, name in ((9, "Ramen Ichiban"), (10, "Ramen Ichiban")):
        decisions[f"1:{serial}"] = ReviewDecision(verdict="accepted", document_type="receipt", name=name,
                                                  date="2025-01-10", time="12:00", cost=900.0, currency="JPY")
    decisions["1:11"] = ReviewDecision(verdict="tossed", document_type="corrupted", name="", date="", time="")
    save_decisions(ingest_dir, decisions)

    plan = ip.plan_archive(ingest_dir)
    moves = {m.key: m.destination for m in plan.moves}
    notes = {m.key: m.note for m in plan.moves}
    assert (notes["1:2"], notes["1:10"], notes["1:1"]) == ("sliced sheet", "crop of 1:2", "")
    assert moves["1:2"] == "tossed/01102025133000_2.png"            # the sheet, kept
    assert moves["1:11"] == "tossed/01102025133000_2.r2c1.0-500-500-1000.png"     # not tossed/slices/...
    ip.run_archive(ingest_dir, scans, _Progress())

    crop = read_sidecar(ingest_dir / moves["1:10"])
    assert (crop.slice_of, crop.slice_cell, crop.slice_box) == ("1:2", [1, 2], Box(x1=500, y1=0, x2=1000, y2=500))
    assert crop.original_filename == "slices/01102025133000_2.r1c2.500-0-1000-500.png" and crop.serial == 10
    sheet = read_sidecar(ingest_dir / moves["1:2"])
    assert sheet.review.sliced and sheet.slice_of is None
    assert read_sidecar(ingest_dir / moves["1:11"]).slice_of == "1:2"


class _Progress:
    cancelled = False
    job_id = "test"

    def set_total(self, total): ...
    def tick(self, ok=True, item="", error=""): assert ok, error
    def say(self, message): ...
    def record(self, run): ...
