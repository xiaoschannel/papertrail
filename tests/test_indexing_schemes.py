"""Scanner filename parsing and batch segmentation."""

from datetime import datetime

from indexing_schemes import canon_imageformula, parse_canon_filename, single_batch_by_filename


def test_parse_canon_filename_valid():
    dt, serial = parse_canon_filename("01102025132642_7.png")
    assert dt == datetime(2025, 1, 10, 13, 26, 42)
    assert serial == 7


def test_parse_canon_filename_invalid():
    assert parse_canon_filename("not_a_scan.png") is None
    assert parse_canon_filename("01102025132642.png") is None       # no serial
    assert parse_canon_filename("0110_5.png") is None               # short timestamp


def test_canon_splits_batch_on_serial_reset():
    files = [
        "01102025132642_1.png",
        "01102025133000_2.png",
        "01102025134000_1.png",  # serial resets -> new batch
        "01102025135000_2.png",
    ]
    batches, skipped, warnings = canon_imageformula(files)
    assert [b.batch_id for b in batches] == [1, 2]
    assert len(batches[0].files) == 2 and len(batches[1].files) == 2
    assert skipped == [] and warnings == []


def test_canon_warns_on_serial_gap():
    files = ["01102025132642_1.png", "01102025133000_3.png"]  # gap 1 -> 3
    batches, _skipped, warnings = canon_imageformula(files)
    assert len(batches) == 1
    assert len(warnings) == 1 and "serial skip" in warnings[0]


def test_canon_skips_unparseable():
    batches, skipped, _w = canon_imageformula(["01102025132642_1.png", "garbage.png"])
    assert skipped == ["garbage.png"]
    assert len(batches) == 1


def test_single_batch_by_filename_sorts_and_serializes():
    batches, skipped, warnings = single_batch_by_filename(["b.png", "a.png", "c.png"])
    assert len(batches) == 1
    assert batches[0].files == {1: "a.png", 2: "b.png", 3: "c.png"}
    assert skipped == [] and warnings == []
