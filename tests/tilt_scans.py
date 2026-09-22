"""The tilt fixtures (``tests/fixtures/tilt``, made by ``tools/build_fixture.py``): printed pages fed in
slightly crooked, with each one's tilt; and turning a page further while a test runs."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TILT = FIXTURES / "tilt"
#: Each fixture's page and how crooked it was fed in (degrees counter-clockwise).
PAGES: dict[str, dict] = json.loads((TILT / "pages.json").read_text(encoding="utf-8"))
#: The back of a page, blank but for specks and a shadow: never to be flagged.
BLANK_BACK = FIXTURES / "orientation" / "blank_back.png"


def scan(name: str) -> Image.Image:
    """A tilt fixture, in greyscale."""
    with Image.open(TILT / name) as img:
        return img.convert("L")


def tilt(img: Image.Image, degrees: float) -> Image.Image:
    """The page turned ``degrees`` counter-clockwise more, on white, as a crooked feed turns it."""
    return img.convert("L").rotate(degrees, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
