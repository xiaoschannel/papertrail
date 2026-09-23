"""Preparing a scan for OCR: its trim first, measured on the file as stored, then the turn and treatment."""

from pathlib import Path

from PIL import Image

from models import Trim
from scan_enhance import Enhancement, crop_to_trim, enhance, trimmed_file


def _banded(path: Path | None = None) -> Image.Image:
    """40 wide, 100 tall: black above row 30, white below."""
    image = Image.new("RGB", (40, 100), "white")
    image.paste((0, 0, 0), (0, 0, 40, 30))
    if path is not None:
        image.save(path)
    return image


def test_a_trim_keeps_only_its_band():
    kept = crop_to_trim(_banded(), Trim(top=0.3, bottom=1.0))
    assert kept.size == (40, 70)
    assert kept.getextrema() == ((255, 255), (255, 255), (255, 255))       # none of the black header
    assert crop_to_trim(_banded(), None).size == (40, 100)


def test_the_trim_cuts_the_file_before_it_is_turned():
    """The band is measured on the file, so a turned preview still keeps the same part of the page."""
    turned = enhance(_banded(), Enhancement(top_points="left", trim=Trim(top=0.3, bottom=1.0)))
    assert turned.size == (70, 40)
    assert turned.getextrema() == ((255, 255), (255, 255), (255, 255))


def test_ocr_reads_a_copy_of_the_band_and_the_copy_is_gone_after(tmp_path):
    scan = tmp_path / "scan.png"
    _banded(scan)

    with trimmed_file(scan, None) as readable:
        assert readable == scan
    with trimmed_file(scan, Trim(top=0.3, bottom=1.0)) as readable:
        assert readable != scan and readable.parent != tmp_path       # never beside the only copy of the scan
        with Image.open(readable) as image:
            assert image.size == (40, 70)
    assert not readable.exists()
    with Image.open(scan) as image:
        assert image.size == (40, 100)                                 # the scan itself is untouched


# --- mending the white lines a thermal head's dead dots leave (tests/fixtures/print_head) ------------------
PRINT_HEAD = Path(__file__).resolve().parent / "fixtures" / "print_head"


def _ink(image: Image.Image):
    import numpy as np

    return np.asarray(image.convert("L")) < 128


def _pieces(ink) -> int:
    """How many separate pieces of ink there are: a stroke cut by a white line counts twice."""
    import cv2
    import numpy as np

    return cv2.connectedComponents(ink.astype(np.uint8), connectivity=8)[0] - 1


def _print_head():
    import json

    meta = json.loads((PRINT_HEAD / "dead_dots.json").read_text(encoding="utf-8"))
    with Image.open(PRINT_HEAD / meta["whole"]) as whole, Image.open(PRINT_HEAD / meta["broken"]) as broken:
        return whole.convert("RGB"), broken.convert("RGB"), meta["dead_columns"]


def test_mending_joins_the_strokes_dead_dots_cut():
    whole, broken, dead = _print_head()
    mended = enhance(broken, Enhancement(treatment="mend"))

    assert mended.size == broken.size and mended.mode == "RGB"
    printed, cut, joined = _pieces(_ink(whole)), _pieces(_ink(broken)), _pieces(_ink(mended))
    assert cut > 4 * printed                          # the lines cut nearly every character apart
    assert joined <= printed * 1.1                    # and mended, it is in one piece again
    lost = _ink(whole)[:, dead]
    assert (_ink(mended)[:, dead] & lost).sum() >= 0.85 * lost.sum()


def test_mending_never_takes_ink_away():
    _, broken, _ = _print_head()
    assert not (_ink(broken) & ~_ink(enhance(broken, Enhancement(treatment="mend")))).any()


def test_mending_leaves_a_gap_between_characters_open():
    """Two strokes five pixels apart, as two characters are, stay apart; one pixel apart, they join."""
    import numpy as np

    page = np.full((20, 40, 3), 255, np.uint8)
    page[:, 10:13] = page[:, 18:21] = 0               # a five-pixel gap
    page[:, 30:33] = page[:, 34:37] = 0               # a one-pixel white line
    mended = _ink(enhance(Image.fromarray(page), Enhancement(treatment="mend")))
    assert not mended[:, 15].any()
    assert mended[:, 33].all()
