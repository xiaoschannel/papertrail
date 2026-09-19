"""Making a stubborn scan readable before OCR runs on it again (the Marked Workshop's controls).

A marked document is usually one the model misread: too dark, washed out, upside down, or printed on
coloured paper. These are the treatments the Streamlit workshop offered, lifted out of the page so the
web app can apply them server-side — the browser asks for a treatment, the server produces the pixels
that OCR actually sees, and the same function draws the preview.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from PIL import Image, ImageEnhance

from document_grouping import ROTATIONS

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


def enhance(image: Image.Image, settings: Enhancement) -> Image.Image:
    """The scan as OCR should see it: rotated upright, then the chosen treatment."""
    working = image.convert("RGB")
    rotation = ROTATIONS.get(settings.top_points)
    if rotation is not None:
        working = working.transpose(rotation)

    if settings.treatment == "clahe":
        return _clahe(working, settings.clip, settings.grid)
    if settings.treatment == "whiten":
        return _whiten_background(working, settings.lightness, settings.chroma)
    if settings.treatment == "contrast":
        return _contrast_gamma(working, settings.contrast, settings.gamma)
    return working


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
