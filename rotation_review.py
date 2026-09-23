"""Fix Rotation's queue, and what it learns from: every decision on a scan's rotation, kept as training data.

A scan is in the queue when the detectors flag it (``orientation`` says it is turned, or ``deskew`` finds it
tilted, measured on it as the suggested turn leaves it) or when Review sent it back because they missed it,
and it leaves the queue when someone decides on it: Fix (turn, then straighten, in one step) or Leave as is.
A decision holds for the version of the scan it was made on, and a fixed scan stays out of the queue even
if the detectors would still flag it. Undo takes a fix back, restoring the scan from the folder's history,
and the scan is judged again.

Every decision is kept, agreements too, with what the detectors said (raw, and the thresholds they were
judged by), what was applied, and ``METHOD``, the version of the detection it tested: in the working file
``ROTATION_DECISIONS`` (folded into each page's sidecar by Archive) and the log ``ROTATION_LOG``, which
keeps them all for good.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

import archive_history
import ingest_pipeline as pipeline
from data import add_rotation_decision, load_rotation_decisions, load_trims, set_trim
from deskew import SkewEstimate
from models import RotationDecision, RotationPrediction, TopPoints
from orientation import MIN_CONFIDENCE, UNREADABLE, OrientationEstimate
from settings import root_of

#: The detection a prediction came from: the orientation model and its threshold, and the tilt estimator
#: judged by how far a tilt shows. Bump it when either changes, so records say which method they tested.
METHOD = "v1"
#: A straightening within this many degrees of the suggestion counts as agreeing with it.
AGREE_DEGREES = 0.3

#: Where a scan's top points (None: the model couldn't be had); and its tilt as turning it upright from
#: ``top_points`` ("" as it is) leaves it.
Orient = Callable[[Path], OrientationEstimate] | None
Skew = Callable[[Path, str], SkewEstimate]


class QueueItem(BaseModel):
    key: str
    batch_id: int
    filename: str
    image_version: int
    #: Review sent it back: the detectors missed it.
    sent_back: bool
    prediction: RotationPrediction


def same_version(shown: int, now: int) -> bool:
    """Whether the version a page sent back is the scan's version now. Versions are mtimes in nanoseconds,
    past what a JavaScript number holds exactly (2**53): the page sends one back rounded by up to a few
    hundred nanoseconds, and two writes of a scan are never that close."""
    return abs(shown - now) <= 1_000


class StaleScan(ValueError):
    """The scan changed since the page showed it: decide on it as it is now."""


def predict(path: Path, orient: Orient, skew: Skew, share: float) -> RotationPrediction:
    """What the detectors say of one scan: the turn, then the tilt of the scan as that turn leaves it."""
    facing = orient(path) if orient is not None else None
    turn = facing.top_points if facing is not None and facing.needs_turning else None
    tilt = skew(path, turn or "")
    return RotationPrediction(
        method=METHOD, top_points=facing.top_points if facing else None, confidence=facing.confidence if facing else None,
        turn=turn, degrees=tilt.degrees, gain=tilt.gain, drift=round(tilt.drift, 4),
        tilt=tilt.degrees if tilt.needs_straightening(share) else None, min_confidence=MIN_CONFIDENCE, tilt_share=share)


def _turnable(output_path: Path, input_path: Path) -> list[tuple[str, int, Path]]:
    """Every scan Fix Rotation can turn, in batch then scan order: not tossed, not a crop (turned the way its
    sheet is) or a sliced sheet (it stays as it was cut), and in the scan folder."""
    index = pipeline._load_index(output_path)
    pages = []
    for batch in (b for b in (index.batches if index else []) if not b.archived):
        state = pipeline.grouping_state(output_path, batch.batch_id)
        for p in state.pages:
            path = input_path / p.filename
            if not p.tossed and p.crop_of is None and not p.sliced and p.serial not in batch.grids and path.is_file():
                pages.append((p.key, batch.batch_id, path))
    return pages


def _decided(decisions: list[RotationDecision], version: int) -> bool:
    """Whether a decision holds for this version of the scan: Fix or Leave as is, on it or making it."""
    latest = decisions[-1] if decisions else None
    return latest is not None and latest.action in ("fixed", "left") and latest.version_after == version


def _sent_back(decisions: list[RotationDecision]) -> bool:
    """Whether the page is waiting as Review sent it back: its last decision, once each undone fix is taken
    back, is the send-back (so undoing its fix puts it back in the queue, not out of it)."""
    standing: list[RotationDecision] = []
    for decision in decisions:
        if decision.action == "undone":
            if standing and standing[-1].action == "fixed":
                standing.pop()
        else:
            standing.append(decision)
    return bool(standing) and standing[-1].action == "sent_back"


def queue(output_path: Path, input_path: Path, orient: Orient, skew: Skew, share: float) -> list[QueueItem]:
    """The scans to decide on, in batch then scan order: flagged or sent back, and not decided on as they are.
    A scan that can't be read (half copied, not an image) is left out rather than failing the rest."""
    decisions = load_rotation_decisions(output_path)
    waiting = []
    for key, batch_id, path in _turnable(output_path, input_path):
        version = path.stat().st_mtime_ns
        if not _decided(decisions.get(key, []), version):
            waiting.append((key, batch_id, path, version))

    def judged(path: Path) -> RotationPrediction | None:
        try:
            return predict(path, orient, skew, share)
        except UNREADABLE:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:   # numpy/OpenCV work, off the GIL
        predictions = list(pool.map(judged, [path for _, _, path, _ in waiting]))
    return [QueueItem(key=key, batch_id=batch_id, filename=path.name, image_version=version,
                      sent_back=_sent_back(decisions.get(key, [])), prediction=prediction)
            for (key, batch_id, path, version), prediction in zip(waiting, predictions)
            if prediction is not None and (prediction.flagged or _sent_back(decisions.get(key, [])))]


def _turnable_page(output_path: Path, key: str) -> str:
    """The filename of a page Fix Rotation can turn (KeyError: no such page; ValueError: one it can't)."""
    batch, serial, filename = pipeline._unarchived_page(output_path, key)
    if serial in batch.slices:
        raise ValueError(f"{key} is a crop; it is turned the way its sheet is. Unslice the sheet to turn it.")
    if pipeline._is_sliced_sheet(output_path, batch, serial, key):
        raise ValueError(f"{key} is sliced; unslice it before turning it, since its crops are cut upright.")
    return filename


def _page(output_path: Path, input_path: Path, key: str) -> tuple[str, Path]:
    """A page Fix Rotation can turn, and its scan (FileNotFoundError: it isn't in the scan folder)."""
    filename = _turnable_page(output_path, key)
    path = input_path / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    return filename, path


def turnable(output_path: Path, key: str) -> bool:
    """Whether Fix Rotation can take the page (Review's send-back): unarchived, not a crop or a sliced sheet."""
    try:
        _turnable_page(output_path, key)
    except (KeyError, ValueError):
        return False
    return True


def _kept(output_path: Path, path: Path) -> str | None:
    """The scan's bytes kept in the folder's history, or None when the folder has none (or no git)."""
    root = root_of(output_path)
    if not (root / ".git").exists():
        return None
    try:
        return archive_history.keep_blob(root, path)
    except archive_history.HistoryError:
        return None


def decide(output_path: Path, input_path: Path, key: str, image_version: int, fix: bool,
           top_points: TopPoints | None, degrees: float, source: str, orient: Orient, skew: Skew,
           share: float) -> RotationDecision:
    """Fix a scan (turn it upright from ``top_points``, then straighten it ``degrees``, measured on it
    turned) or leave it as it is, and keep the decision. Refused (``StaleScan``) when the scan isn't the
    version the page showed."""
    filename, path = _page(output_path, input_path, key)
    current = path.stat().st_mtime_ns
    if not same_version(image_version, current):
        raise StaleScan(f"{key} changed since it was shown: look at it again.")
    if fix and top_points is None and not degrees:
        raise ValueError("a fix turns or straightens the scan: choose a turn or a tilt")
    prediction = predict(path, orient, skew, share)
    before = trim_before = None
    if fix:
        before = _kept(output_path, path)
        trim_before = load_trims(output_path).get(key)
        if top_points is not None:
            pipeline.rotate_page_image(output_path, input_path, key, top_points)
        if degrees:
            pipeline.straighten_page_image(output_path, input_path, key, degrees)
        agrees = top_points == prediction.turn and abs(degrees - (prediction.tilt or 0.0)) <= AGREE_DEGREES
    else:
        top_points, degrees = None, 0.0
        agrees = not prediction.flagged
    decision = RotationDecision(
        at=time.time(), key=key, filename=filename, source=source, action="fixed" if fix else "left",  # type: ignore[arg-type]
        top_points=top_points, degrees=degrees, predicted=prediction, agrees=agrees, image_version=current,
        version_after=path.stat().st_mtime_ns, before=before, trim_before=trim_before)
    add_rotation_decision(output_path, decision)
    return decision


def undo(output_path: Path, input_path: Path, key: str) -> RotationDecision:
    """Take back a page's last decision when it was a fix: the scan's bytes and trim as they were before it.
    What was read from the fixed scan is forgotten, and the scan is judged again."""
    filename, path = _page(output_path, input_path, key)
    decisions = load_rotation_decisions(output_path).get(key, [])
    last = decisions[-1] if decisions else None
    if last is None or last.action != "fixed":
        raise ValueError(f"{key}'s last decision isn't a fix: there is nothing to undo.")
    if last.before is None:
        raise ValueError(f"{key} was fixed without the folder's history, so its scan as it was isn't kept.")
    version = path.stat().st_mtime_ns
    if version != last.version_after:
        raise StaleScan(f"{key} changed since it was fixed: undoing would lose that.")
    data = archive_history.read_blob(root_of(output_path), last.before)
    swap = path.with_name(f"{path.stem}.rotating{path.suffix}")     # ignored by the history, like a turn's
    try:
        swap.write_bytes(data)
        os.replace(swap, path)
    finally:
        swap.unlink(missing_ok=True)       # never left beside the scans, where File Index would take it for one
    set_trim(output_path, key, last.trim_before)
    batch, _, _ = pipeline._unarchived_page(output_path, key)
    pipeline._forget_reading(output_path, batch, key)
    decision = RotationDecision(at=time.time(), key=key, filename=filename, source=last.source, action="undone",
                                image_version=version, version_after=path.stat().st_mtime_ns)
    add_rotation_decision(output_path, decision)
    return decision


def send_back(output_path: Path, input_path: Path, key: str, orient: Orient, skew: Skew,
              share: float) -> RotationDecision:
    """Review found a page the detectors missed: what was read from it is forgotten (its document leaves
    Review until it is read again), and it joins Fix Rotation's queue."""
    filename, path = _page(output_path, input_path, key)
    prediction = predict(path, orient, skew, share)
    batch, _, _ = pipeline._unarchived_page(output_path, key)
    pipeline._forget_reading(output_path, batch, key)
    version = path.stat().st_mtime_ns
    decision = RotationDecision(at=time.time(), key=key, filename=filename, source="review", action="sent_back",
                                predicted=prediction, agrees=False, image_version=version, version_after=version)
    add_rotation_decision(output_path, decision)
    return decision


def recent(output_path: Path, input_path: Path, limit: int = 20) -> list[tuple[RotationDecision, bool]]:
    """The latest decisions on pages still being ingested, newest first, each with whether Undo can take it
    back now (a page's last decision, a fix, with its scan kept and unchanged since)."""
    index = pipeline._load_index(output_path)
    unarchived = {b.batch_id for b in (index.batches if index else []) if not b.archived}
    decisions = load_rotation_decisions(output_path)
    rows = [(d, d is ds[-1]) for key, ds in decisions.items() if int(key.split(":")[0]) in unarchived for d in ds]
    rows.sort(key=lambda row: row[0].at, reverse=True)

    def undoable(d: RotationDecision, last: bool) -> bool:
        if not last or d.action != "fixed" or d.before is None:
            return False
        path = input_path / d.filename
        return path.is_file() and path.stat().st_mtime_ns == d.version_after

    return [(d, undoable(d, last)) for d, last in rows[:limit]]
