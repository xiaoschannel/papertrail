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
