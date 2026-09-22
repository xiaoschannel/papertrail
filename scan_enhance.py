"""Making a stubborn scan readable before OCR runs on it again (the Marked Workshop's controls).

A marked document is usually one the model misread: too dark, washed out, upside down, or printed on
coloured paper. The treatments are applied server-side — the browser asks for a treatment, the server
produces the pixels that OCR actually sees, and the same function draws the preview.

A page's trim (``models.Trim``) is applied here too, and first: it is measured on the file as stored,
so it cuts before the page is turned, and the treatment then works only on what OCR will read.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageEnhance

from document_grouping import ROTATIONS
from models import Trim

Treatment = Literal["none", "clahe", "contrast", "whiten"]


@dataclass
class Enhancement:
    """How to prepare a scan. ``top_points`` is where the page's top currently points, as in File Index."""

    top_points: str = ""                 # "", "left", "right", "down"
    treatment: Treatment = "none"
    clip: float = 3.0                    # CLAHE
    grid: int = 8
    contrast: float = 2.5                # contrast + gamma
    gamma: float = 0.5
    lightness: int = 200                 # whiten background
    chroma: int = 10
    denoise_before: bool = False         # smooth grain before the treatment, after it, or both
    denoise_after: bool = False          # (Experiment only; the Workshop doesn't offer it)
    denoise_strength: int = 6
    trim: Trim | None = None           # the page's own trim (not a control: it is kept with the page)


def crop_to_trim(image: Image.Image, band: Trim | None) -> Image.Image:
    """The part of the scan a trim keeps (all of it without one)."""
    if band is None:
        return image
    first, end = band.rows(image.height)
    return image.crop((0, first, image.width, end))


@contextmanager
def trimmed_file(path: Path, band: Trim | None) -> Iterator[Path]:
    """``path`` as OCR should read it: the file itself, or a temporary copy of the band a trim keeps.

    The OCR models take a file, not pixels, and the scan is the only copy, so a trim is read from a
    lossless copy that is deleted afterwards.
    """
    if band is None:
        yield path
        return
    handle, name = tempfile.mkstemp(prefix=f"{path.stem}.", suffix=".trimmed.png")
    os.close(handle)
    copy = Path(name)
    try:
        with Image.open(path) as image:
            crop_to_trim(image.convert("RGB"), band).save(copy, format="PNG")
        yield copy
    finally:
        copy.unlink(missing_ok=True)


def enhance(image: Image.Image, settings: Enhancement) -> Image.Image:
    """The scan as OCR should see it: trimmed, rotated upright, then the chosen treatment (denoised
    around it if asked)."""
    working = crop_to_trim(image.convert("RGB"), settings.trim)
    rotation = ROTATIONS.get(settings.top_points)
    if rotation is not None:
        working = working.transpose(rotation)
    if settings.denoise_before:
        working = _denoise(working, settings.denoise_strength)

    if settings.treatment == "clahe":
        working = _clahe(working, settings.clip, settings.grid)
    elif settings.treatment == "whiten":
        working = _whiten_background(working, settings.lightness, settings.chroma)
    elif settings.treatment == "contrast":
        working = _contrast_gamma(working, settings.contrast, settings.gamma)

    if settings.denoise_after:
        working = _denoise(working, settings.denoise_strength)
    return working


def _denoise(image: Image.Image, strength: int) -> Image.Image:
    """Smooth scanner grain and paper texture (non-local means), which OCR can read as specks of text."""
    import cv2

    return Image.fromarray(cv2.fastNlMeansDenoisingColored(np.array(image), None, strength, strength, 7, 21))


def _clahe(image: Image.Image, clip: float, grid: int) -> Image.Image:
    """Local contrast, on lightness only, so faded thermal print comes back without colour shifts."""
    import cv2

    lab = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(max(2, grid), max(2, grid))).apply(lab[:, :, 0])
    return Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))


def _whiten_background(image: Image.Image, lightness: int, chroma: int) -> Image.Image:
    """Blank out light coloured areas — tinted receipt paper that OCR reads as noise."""
    import cv2

    lab = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2LAB)
    lightness_channel, a, b = lab[:, :, 0], lab[:, :, 1], lab[:, :, 2]
    colourfulness = np.maximum(np.abs(a.astype(np.int32) - 128), np.abs(b.astype(np.int32) - 128))
    mask = (lightness_channel >= lightness) & (colourfulness >= chroma)
    lab[mask, 0] = 255
    lab[mask, 1] = 128
    lab[mask, 2] = 128
    return Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))


def _contrast_gamma(image: Image.Image, contrast: float, gamma: float) -> Image.Image:
    """Push contrast and lift the mid-tones — the blunt instrument for a grey, low-ink scan."""
    working = ImageEnhance.Contrast(image).enhance(contrast)
    if gamma != 1.0 and gamma > 0:
        lut = [int(((value / 255.0) ** (1.0 / gamma)) * 255) for value in range(256)]
        working = working.point(lut * 3)
    return working
