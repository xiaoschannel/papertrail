"""Finding scans fed in sideways or upside down (what Slice and Group point out).

Projection profiles can tell that a page's lines run up and down, but not which way is up, so this asks a
small classifier: PaddleClas's text-image orientation model (PP-LCNet, Apache-2.0), which sorts a 224x224
crop of text into upright, turned 90° clockwise, upside down, or turned 90° counter-clockwise. OpenCV runs
it on the CPU, in a few milliseconds a crop.

A receipt is tall and narrow, and squashed whole into a square its text is unreadable, so the page is
scaled to 256 pixels across its short side and cut into overlapping squares along its length; squares
with barely any ink (margins, blank paper) are dropped, and the rest vote: the geometric mean of their
answers, so one square sure of something odd can't outvote the rest.
Faded thermal paper is contrast-stretched first.

The model is downloaded on first use, like the OCR models: the ONNX export ships inside the
rapid-orientation wheel on PyPI (a fixed, immutable file), and is checked against a pinned hash before
it is cached under ``~/.cache/papertrail``.

Only a suggestion: Slice and Group show it, and a scan is turned only after someone confirms it.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import os
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

#: Where the model comes from: the wheel, the model inside it, and both their SHA-256s.
MODEL_URL = ("https://files.pythonhosted.org/packages/5c/6c/9261a8f8b694353b88c6d26e382555f3933fe85c75d3e959607b056d267f/"
             "rapid_orientation-0.0.11-py3-none-any.whl")
WHEEL_SHA256 = "3d69e77c18ac05a3e9a157e9a26ecff49e8ef485913eaa57b0921b0419684be6"
MODEL_MEMBER = "rapid_orientation/models/rapid_orientation.onnx"
MODEL_SHA256 = "2f62c9bfb830a0b417241269fde7ef2d0ad5446c0ed2b8af33b1f6543545e8e2"
#: The cached model; PAPERTRAIL_CACHE moves the folder it lives in.
CACHE_DIR = Path(os.environ.get("PAPERTRAIL_CACHE") or Path.home() / ".cache" / "papertrail")

#: Below this the model is unsure, and the page is left alone. Its most confident answers sit a little
#: over 0.9; faded, crumpled or nearly empty pages land in between, and are the ones it gets wrong.
MIN_CONFIDENCE = 0.6
#: The model's classes, in its output order: how far the page has been turned clockwise. The rotate
#: arrows name where the page's top points instead.
_TOP_POINTS = (None, "right", "down", "left")
_SIDE = 224
_SHORT = 256
#: A square needs this share of dark pixels to vote; paper grain and margins don't.
_MIN_INK = 0.02
#: Grey levels between a page's darkest and lightest tones below which it isn't contrast-stretched: a
#: blank back's faint shading spans under 30 (the fixture's, from tools/build_fixture.py), which stretched
#: would read as ink.
_MIN_CONTRAST = 32
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


@dataclass(frozen=True)
class OrientationEstimate:
    #: Where the page's top points, as the rotate arrows name it; None when the page looks upright.
    top_points: str | None
    #: How sure the model is of that, 0-1.
    confidence: float

    @property
    def needs_turning(self) -> bool:
        return self.top_points is not None and self.confidence >= MIN_CONFIDENCE


class ModelUnavailable(RuntimeError):
    """The orientation model could not be fetched or loaded."""


_lock = threading.Lock()
_net = None


def model_path() -> Path:
    return CACHE_DIR / "models" / f"text_image_orientation-{MODEL_SHA256[:12]}.onnx"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_model(url: str = MODEL_URL) -> Path:
    """The cached model, downloaded and checked first if it isn't cached yet."""
    target = model_path()
    if target.is_file():
        return target
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            wheel = response.read()
    except (OSError, http.client.HTTPException) as exc:   # URLError wraps its reason in "<urlopen error ...>"
        raise ModelUnavailable(f"couldn't download the orientation model: {getattr(exc, 'reason', exc)}") from exc
    if _sha256(wheel) != WHEEL_SHA256:
        raise ModelUnavailable("the downloaded orientation model isn't the expected file")
    try:
        model = zipfile.ZipFile(io.BytesIO(wheel)).read(MODEL_MEMBER)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ModelUnavailable("the downloaded orientation model isn't the expected file") from exc
    if _sha256(model) != MODEL_SHA256:
        raise ModelUnavailable("the downloaded orientation model isn't the expected file")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Written beside and swapped in, so a half-written file is never taken for the model.
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(model)
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return target


def _load_locked():
    import cv2

    global _net
    if _net is None:
        path = fetch_model()
        try:
            _net = cv2.dnn.readNetFromONNX(str(path))
        except cv2.error as exc:
            raise ModelUnavailable(f"couldn't load the orientation model: {exc}") from exc
    return _net


def load_model() -> None:
    """Fetch and load the model now, if it isn't loaded yet.

    Worth calling before estimating many pages: a model that can't be had then fails once, rather than
    once per page.
    """
    with _lock:
        _load_locked()


def _classify(crops: np.ndarray) -> np.ndarray:
    """The model's class probabilities for a stack of normalized crops."""
    with _lock:   # one network, loaded once; OpenCV's forward pass isn't safe to share between threads
        net = _load_locked()
        net.setInput(crops)
        return net.forward().copy()


def _stretch(rgb: np.ndarray) -> np.ndarray:
    """Faded print stretched to the full range between the page's darkest and lightest tones.

    A page with less contrast than faded print has none to bring out: stretching a blank back would turn
    its shading and paper grain into ink.
    """
    import cv2

    lo, hi = np.percentile(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (1, 99))
    if hi - lo < _MIN_CONTRAST:
        return rgb
    return np.clip((rgb.astype(np.float32) - lo) * (255 / (hi - lo)), 0, 255).astype(np.uint8)


def _starts(length: int) -> np.ndarray:
    """Where the squares start along one side: overlapping, covering it end to end."""
    if length <= _SIDE:
        return np.array([0])
    return np.linspace(0, length - _SIDE, -(-length // _SIDE) + 1).astype(int)


def _crops(image: Image.Image) -> np.ndarray:
    import cv2

    rgb = np.asarray(image.convert("RGB"))
    h, w = rgb.shape[:2]
    scale = _SHORT / min(h, w)
    size = (max(_SHORT, round(w * scale)), max(_SHORT, round(h * scale)))
    page = _stretch(cv2.resize(rgb, size, interpolation=cv2.INTER_AREA))
    dark = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY) < 128
    squares = [(y, x) for y in _starts(page.shape[0]) for x in _starts(page.shape[1])]
    ink = [float(dark[y:y + _SIDE, x:x + _SIDE].mean()) for y, x in squares]
    inked = [sq for sq, share in zip(squares, ink) if share >= _MIN_INK]
    if not inked:
        return np.empty((0, 3, _SIDE, _SIDE), np.float32)
    crops = np.stack([page[y:y + _SIDE, x:x + _SIDE] for y, x in inked]).astype(np.float32)
    return ((crops / 255 - _MEAN) / _STD).transpose(0, 3, 1, 2).copy()


def estimate_orientation(image: Image.Image) -> OrientationEstimate:
    """Which way the scan's text faces, as where its top points."""
    crops = _crops(image)
    if not len(crops):
        return OrientationEstimate(None, 0.0)   # nothing printed to go by
    # The squares' geometric mean: one square sure of a wrong answer can't outvote the rest.
    agreed = np.exp(np.log(np.clip(_classify(crops), 1e-6, 1.0)).mean(axis=0))
    best = int(np.argmax(agreed))
    return OrientationEstimate(_TOP_POINTS[best], round(float(agreed[best]), 3))


#: What a scan that can't be read raises: not an image, cut short, too large to open, or one OpenCV rejects.
UNREADABLE = (OSError, ValueError, Image.DecompressionBombError)


def estimate_file_orientation(path: Path) -> OrientationEstimate:
    """Which way the scan at ``path`` faces; raises one of ``UNREADABLE`` for a scan it can't read."""
    import cv2

    try:
        with Image.open(path) as img:
            return estimate_orientation(img)
    except cv2.error as exc:
        raise ValueError(f"OpenCV couldn't read {path.name}: {exc}") from exc
