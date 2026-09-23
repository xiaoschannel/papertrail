"""Finding and fixing a scan fed in at a slight angle (the "looks tilted" suggestions on Fix Rotation).

A receipt that went through the feeder a few degrees off comes out with its text lines sloping. The
estimate is a projection profile: rotate the page's ink through candidate angles and keep the one whose
row sums change most sharply from row to row, which is when every text line lies on one row band. It
needs no model and runs on the CPU in well under a tenth of a second a page.

Only a suggestion: Fix Rotation shows it, and the scan is rewritten only after someone confirms it.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict

from document_grouping import replace_image, rotate_upright
from models import Trim, trim_of

#: The steepest tilt looked for either way. A page further off than this is a sideways scan, not a
#: slightly crooked one, and the rotate arrows are the fix for that.
MAX_DEGREES = 15.0
#: A tilt worth fixing, unless the Config page says otherwise: how far the page's long edge wanders
#: sideways along its length, as a share of its short edge. That is the empty wedge a crooked feed leaves
#: along the long side, so the same angle counts for more on a long receipt than on a short one.
DEFAULT_TILT_SHARE = 0.03
#: Under this the estimate can't place a tilt reliably, whatever the page's shape.
MIN_DEGREES = 0.3
#: How much sharper the rows must be at the best angle than as scanned: a nearly blank page scores
#: about the same at every angle, and its "best" angle is noise.
MIN_GAIN = 1.3
#: The page is scored at about this many pixels; enough to place a text line to well under a degree.
_WORK_PIXELS = 600_000
_COARSE_STEP = 0.5
#: What straightening leaves around the ink at least, as a share of the scan's shorter side.
_INK_MARGIN = 0.01
#: Ink smaller than this many working pixels (a speck of dust) doesn't hold a crop back.
_SPECK = 3
#: The shortest side, in working pixels, a page must have to be measured at all.
_MIN_SIDE = 16
_FINE_STEP = 0.05


class SkewEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Degrees to turn the scan counter-clockwise to level its text (negative: clockwise).
    degrees: float
    #: Row sharpness at ``degrees`` over row sharpness as scanned (1.0: no better than as scanned).
    gain: float
    #: False when the best angle sat at the edge of the search, so the true one may lie beyond it.
    within_range: bool
    #: The size of the ink's area in scan pixels, specks left out: the page's shape as printed, without the
    #: scanner bed or margins round it, which says how far the tilt shows (``drift``).
    width: int = 0
    height: int = 0

    @property
    def drift(self) -> float:
        """How far the long edge wanders sideways along its length, as a share of the short edge."""
        long, short = max(self.width, self.height), min(self.width, self.height)
        return abs(math.sin(math.radians(self.degrees))) * (long / short if short else 1.0)

    def needs_straightening(self, share: float = DEFAULT_TILT_SHARE) -> bool:
        """Whether the tilt is worth fixing: it shows (``drift`` at least ``share``), it is big enough to be
        measured, and the text really is sharper levelled."""
        return (self.within_range and self.gain >= MIN_GAIN and abs(self.degrees) >= MIN_DEGREES
                and self.drift >= share)


def _ink(image: Image.Image) -> np.ndarray:
    """A float mask of the page's dark marks at working size."""
    import cv2

    gray = np.asarray(image.convert("L"))
    scale = min(1.0, (_WORK_PIXELS / gray.size) ** 0.5)
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Capped, so a near-blank page's paper grain and faint shading don't count as ink.
    # At or under the threshold, as OpenCV splits it: a black-and-white scan's threshold is its black itself.
    return (gray <= min(otsu, 200)).astype(np.float32)


def _sharpness(ink: np.ndarray, degrees: float) -> float:
    import cv2

    h, w = ink.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)   # positive: counter-clockwise
    rows = cv2.warpAffine(ink, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=0).sum(axis=1)
    return float(np.square(np.diff(rows)).sum())


def _ink_extent(ink: np.ndarray, image: Image.Image) -> tuple[int, int]:
    """The width and height of the area the ink covers, specks left out, in the scan's own pixels."""
    import cv2

    _, _, stats, _ = cv2.connectedComponentsWithStats((ink > 0).astype(np.uint8), connectivity=8)
    marks = stats[1:][stats[1:, cv2.CC_STAT_AREA] >= _SPECK]
    if len(marks) == 0:
        return image.width, image.height
    left, top = marks[:, cv2.CC_STAT_LEFT].min(), marks[:, cv2.CC_STAT_TOP].min()
    right = (marks[:, cv2.CC_STAT_LEFT] + marks[:, cv2.CC_STAT_WIDTH]).max()
    bottom = (marks[:, cv2.CC_STAT_TOP] + marks[:, cv2.CC_STAT_HEIGHT]).max()
    scale = image.width / ink.shape[1]
    return round((right - left) * scale), round((bottom - top) * scale)


def estimate_skew(image: Image.Image) -> SkewEstimate:
    """How far the scan's text lines slope, as the turn that levels them."""
    import cv2

    ink = _ink(image)
    # Too small to have lines to level (a placeholder, a sliver): halving it for the wide search would leave
    # nothing, which OpenCV refuses.
    if not ink.any() or min(ink.shape) < _MIN_SIDE:
        return SkewEstimate(degrees=0.0, gain=1.0, within_range=True, width=image.width, height=image.height)
    # The wide search runs at half size, where a text line still spans rows; the fine one at full size, two
    # steps either side: as scanned (0°) is the one angle not blurred by turning, so the wide search leans to
    # it, and a tilt just under a degree can come out there.
    small = cv2.resize(ink, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    coarse = np.arange(-MAX_DEGREES, MAX_DEGREES + _COARSE_STEP / 2, _COARSE_STEP)
    best = float(coarse[int(np.argmax([_sharpness(small, a) for a in coarse]))])
    fine = np.arange(best - 2 * _COARSE_STEP, best + 2 * _COARSE_STEP + _FINE_STEP / 2, _FINE_STEP)
    scores = [_sharpness(ink, a) for a in fine]
    degrees = float(np.clip(fine[int(np.argmax(scores))], -MAX_DEGREES, MAX_DEGREES))
    as_scanned = _sharpness(ink, 0.0)
    gain = max(scores) / as_scanned if as_scanned > 0 else 1.0
    width, height = _ink_extent(ink, image)
    return SkewEstimate(degrees=round(degrees, 2) + 0.0, gain=round(gain, 2),   # + 0.0: no -0.0
                        within_range=abs(best) < MAX_DEGREES, width=width, height=height)


def estimate_file_skew(path: Path, top_points: str = "") -> SkewEstimate:
    """The tilt of the scan at ``path`` as turning it upright from ``top_points`` would leave it ("" as it is):
    a sideways scan's lines run down the page, and only turned upright do they have a tilt to measure."""
    with Image.open(path) as img:
        return estimate_skew(rotate_upright(img, top_points))


def _background(image: Image.Image) -> int | tuple[int, ...]:
    """The scanner background, from the median of the page's outermost pixels."""
    pixels = np.asarray(image)
    edge = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]])
    median = np.median(edge, axis=0)
    return int(median) if np.ndim(median) == 0 else tuple(int(v) for v in median)


Size = tuple[int, int]
#: How far the middle of what straightening kept lies from the middle of the canvas it turned the scan onto
#: (pixels, right and down): the crop isn't always centred, since it keeps clear of the ink.
Shift = tuple[float, float]
#: A crop on that canvas: left, top, right, bottom (pixels).
Box = tuple[int, int, int, int]


def grown_size(size: Size, degrees: float) -> Size:
    """The canvas a scan of ``size`` is turned onto to keep every corner (Pillow's ``rotate(expand=True)``,
    to the pixel): its turned corners' extent, each end rounded outwards."""
    width, height = size
    theta = -math.radians(degrees)
    cos, sin = round(math.cos(theta), 15), round(math.sin(theta), 15)
    corners = ((0, 0), (width, 0), (width, height), (0, height))
    xs = [cos * (x - width / 2) + sin * (y - height / 2) + width / 2 for x, y in corners]
    ys = [-sin * (x - width / 2) + cos * (y - height / 2) + height / 2 for x, y in corners]
    return math.ceil(max(xs)) - math.floor(min(xs)), math.ceil(max(ys)) - math.floor(min(ys))


def levelled_size(size: Size, degrees: float) -> Size | None:
    """The page a scan of ``size`` holds once turned ``degrees`` level, taking the scan to be the page's
    bounding box as it was fed in crooked (a feeder crops it so): solving ``W = a·cos + b·sin``,
    ``H = a·sin + b·cos`` for the page's ``a`` by ``b``. None when no page fits (a sliver turned further
    than its own shape allows), or for no turn."""
    if not degrees:
        return None
    width, height = size
    theta = np.radians(degrees)
    cos, sin = abs(float(np.cos(theta))), abs(float(np.sin(theta)))
    det = cos * cos - sin * sin
    a, b = (width * cos - height * sin) / det, (height * cos - width * sin) / det
    if a < 1 or b < 1:
        return None
    return round(a), round(b)


def ink_outline(image: Image.Image) -> list[tuple[float, float]]:
    """Where a scan's ink is: the corners of its convex hull, as fractions of the scan's width and height.
    Straightening crops nothing inside it. Specks of dust don't count. Empty for a scan with no ink."""
    import cv2

    ink = (_ink(image) > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    specks = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] < _SPECK)
    ink[np.isin(labels, specks[specks > 0])] = 0
    points = cv2.findNonZero(ink)
    if points is None:
        return []
    height, width = ink.shape
    hull = cv2.convexHull(points)[:, 0, :]
    # each pixel's far edges too, so the hull holds the ink rather than the ink's pixel centres
    corners = {(x + dx, y + dy) for x, y in hull.tolist() for dx in (0, 1) for dy in (0, 1)}
    outline = cv2.convexHull(np.array(sorted(corners), dtype=np.int32))[:, 0, :]
    return [(round(x / width, 4), round(y / height, 4)) for x, y in outline.tolist()]


def straightened_crop(size: Size, degrees: float, outline: list[tuple[float, float]]) -> Box | None:
    """What straightening a scan of ``size`` by ``degrees`` keeps of the canvas it is turned onto
    (``grown_size``): the page it holds, levelled (``levelled_size``), but never less than the ink
    (``outline``) and a margin round it. A scanner that cut through the page leaves the ink against the
    scan's edge, and the page is then taken to be smaller than it is: the ink holds the crop back. None when
    nothing is cropped (no page fits)."""
    page = levelled_size(size, degrees)
    if page is None:
        return None
    grown = grown_size(size, degrees)
    left, top = (grown[0] - page[0]) / 2, (grown[1] - page[1]) / 2
    right, bottom = left + page[0], top + page[1]
    if outline:
        width, height = size
        turned = [_turned_point(x * width, y * height, degrees, size, grown) for x, y in outline]
        margin = _INK_MARGIN * min(size)
        left = min(left, min(x for x, _ in turned) - margin)
        top = min(top, min(y for _, y in turned) - margin)
        right = max(right, max(x for x, _ in turned) + margin)
        bottom = max(bottom, max(y for _, y in turned) + margin)
    return (max(0, math.floor(left)), max(0, math.floor(top)),
            min(grown[0], math.ceil(right)), min(grown[1], math.ceil(bottom)))


def _straightened(image: Image.Image, degrees: float) -> tuple[Image.Image, Shift]:
    working = image if image.mode in ("L", "RGB") else image.convert("RGB")
    turned = working.rotate(degrees, resample=Image.Resampling.BICUBIC, expand=True,
                            fillcolor=_background(working))
    box = straightened_crop(working.size, degrees, ink_outline(working))
    if box is None:
        return turned, (0.0, 0.0)
    shift = ((box[0] + box[2]) / 2 - turned.width / 2, (box[1] + box[3]) / 2 - turned.height / 2)
    return turned.crop(box), shift


def straighten(image: Image.Image, degrees: float) -> Image.Image:
    """The scan turned ``degrees`` counter-clockwise and cropped (``straightened_crop``) to the page it
    holds, levelled, keeping all its ink: the corners the turn swings out, background around a crooked
    page, are left off. When no page fits, the canvas grows to keep every corner; new corners are filled
    with the scanner background."""
    return _straightened(image, degrees)[0]


def straighten_file(path: Path, degrees: float) -> tuple[Size, Size, Shift] | None:
    """Straighten the scan at ``path`` in place. Returns its size before and after, and where what was kept
    lies (``Shift``), which moving its trim with it needs; or None when a turn of 0 left it alone."""
    if not -MAX_DEGREES <= degrees <= MAX_DEGREES:
        raise ValueError(f"a tilt of {degrees}° is beyond the {MAX_DEGREES}° this corrects")
    if not degrees:
        return None
    placed: list[tuple[Size, Size, Shift]] = []

    def turn(img: Image.Image) -> Image.Image:
        turned, shift = _straightened(img, degrees)
        placed.append((img.size, turned.size, shift))
        return turned

    replace_image(path, turn)
    return placed[0]


#: OCR box coordinates are on this scale of the page image, whatever its size.
def _turned_point(x: float, y: float, degrees: float, before: Size, after: Size,
                  shift: Shift = (0.0, 0.0)) -> tuple[float, float]:
    """A point on the scan (pixels), where it lands on the scan straightened by ``degrees``."""
    (w0, h0), (w1, h1) = before, after
    theta = np.radians(degrees)
    cos, sin = float(np.cos(theta)), float(np.sin(theta))
    dx, dy = x - w0 / 2, y - h0 / 2
    return w1 / 2 + dx * cos + dy * sin - shift[0], h1 / 2 - dx * sin + dy * cos - shift[1]


def straightened_trim(band: Trim | None, degrees: float, before: Size, after: Size,
                      shift: Shift = (0.0, 0.0)) -> Trim | None:
    """A page's trim once its scan is straightened: each cut, a line across the page, turns with it and
    comes out sloping, so the band's new cuts are taken at the outer end of each, and it keeps all it held
    (a sliver more at one side). An edge that wasn't cut stays the page's edge."""
    if band is None:
        return None
    (w0, h0), (_, h1) = before, after
    ends = lambda frac: [_turned_point(x, frac * h0, degrees, before, after, shift)[1] / h1 for x in (0, w0)]
    top = 0.0 if band.top == 0 else max(0.0, min(ends(band.top)))
    bottom = 1.0 if band.bottom == 1 else min(1.0, max(ends(band.bottom)))
    return trim_of(top, bottom)
