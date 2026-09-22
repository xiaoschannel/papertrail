"""One-off migration: find archived scans that were fed in slightly crooked, and straighten the ones confirmed.

Scans filed before Slice and Group pointed tilted pages out may still be crooked. This looks at every scan
under the output folder (filed documents, marked/ and tossed/) and lists the ones that look tilted; the
Straighten Archive dev page shows them, and each is straightened only when someone confirms it, at the
turn they settle on. Unarchived batches aren't looked at here: Slice and Group point theirs out.

The archived scan is the only copy, so before one is straightened it and its sidecar are copied under
``.straighten-backup/`` in the output folder, and Undo puts both back. Straightening rewrites the scan
(as Group's Straighten does, cropped to the levelled page) and moves what its sidecar measures on it: the
trim, and the OCR boxes with the band they were read from, keeping their order, so field boxes still land on
their text and the extraction's field sources stay valid. Each scan's total turn is kept beside the backups,
and every straightening is done again from the backup, so turning a scan a second time doesn't resample it
twice. Redo applies the current straightening to scans already straightened (the first ones were saved on
a grown canvas, not cropped). Finalize deletes the backups once the result has been checked; the migration
isn't done until it has.

Temporary: kept as a git tag, not part of the shipped feature.
"""

from __future__ import annotations

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from data import _iter_year_month_dirs, read_sidecar, sidecar_path_for, write_sidecar
from deskew import (
    MAX_DEGREES, MIN_GAIN, estimate_file_skew, grown_size, straighten_boxes, straighten_file, straightened_trim,
)
from settings import IMAGE_EXTENSIONS

BACKUP_DIR = ".straighten-backup"
#: Each straightened scan's total turn from its backup (degrees counter-clockwise), in the backup folder.
TURNS = "turns.json"
#: Tilts this small are listed too, behind the page's "milder tilts" switch: the detector's own cutoff
#: (deskew.MIN_DEGREES) is for flagging, and a filed scan the eye finds crooked may sit under it.
MIN_LISTED = 0.5


def archived_scans(output_path: Path) -> list[Path]:
    """Every scan under the output folder: filed documents, then marked/ and tossed/."""
    folders = sorted(_iter_year_month_dirs(output_path)) + [output_path / "marked", output_path / "tossed"]
    return [path for folder in folders if folder.is_dir() for path in sorted(folder.iterdir())
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.endswith(".enhanced.png")]


class Found(BaseModel):
    rel_path: str
    #: Degrees to turn it counter-clockwise to level it (negative: clockwise).
    degrees: float
    #: The batch it was scanned in (from its sidecar), so the page can leave out batches whose tilts were
    #: already fixed by hand in the scanner software (their shadows still read as a tilt); None if unknown.
    batch_id: int | None = None


class Scan(BaseModel):
    """How far a look through the archive has got, and what it has found so far."""
    running: bool = False
    done: int = 0
    total: int = 0
    unreadable: int = 0
    error: str | None = None
    found: list[Found] = Field(default_factory=list)


_lock = threading.Lock()
_scan = Scan()


def scan_state() -> Scan:
    with _lock:
        return _scan.model_copy(update={"found": list(_scan.found)})


def start_scan(output_path: Path) -> bool:
    """Look through the archive in the background; False if a look is already under way."""
    global _scan
    with _lock:
        if _scan.running:
            return False
        _scan = Scan(running=True)
    threading.Thread(target=_run_scan, args=(output_path,), name="straighten-migration", daemon=True).start()
    return True


def _batch_of(path: Path) -> int | None:
    try:
        sidecar = read_sidecar(path)
    except ValueError:   # a sidecar that won't parse: the page shows it as of no known batch
        return None
    return sidecar.batch_id if sidecar is not None else None


def _listed(degrees: float, gain: float, within_range: bool) -> bool:
    return within_range and gain >= MIN_GAIN and abs(degrees) >= MIN_LISTED


def _sort(found: list[Found]) -> None:
    found.sort(key=lambda f: -abs(f.degrees))   # most crooked first


def _run_scan(output_path: Path) -> None:
    try:
        scans = archived_scans(output_path)
        with _lock:
            _scan.total = len(scans)

        def one(path: Path) -> None:
            try:
                estimate = estimate_file_skew(path)
            except Exception:   # a scan that can't be read or measured is counted, and the look goes on
                with _lock:
                    _scan.unreadable += 1
                    _scan.done += 1
                return
            with _lock:
                _scan.done += 1
                listed = _listed(estimate.degrees, estimate.gain, estimate.within_range)
            if listed:
                found = Found(rel_path=path.relative_to(output_path).as_posix(), degrees=estimate.degrees,
                              batch_id=_batch_of(path))
                with _lock:
                    _scan.found.append(found)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(one, scans))
    except Exception as exc:   # shown on the page; the look can be started again
        with _lock:
            _scan.error = str(exc) or type(exc).__name__
    finally:
        with _lock:
            _sort(_scan.found)
            _scan.running = False


def _archived(output_path: Path, rel_path: str) -> Path:
    root = output_path.resolve()
    path = (root / rel_path).resolve()
    if not path.is_relative_to(root) or path.is_relative_to(root / BACKUP_DIR) or not path.is_file():
        raise KeyError(f"no archived scan {rel_path}")
    return path


def _backup(output_path: Path, rel_path: str) -> Path:
    return output_path / BACKUP_DIR / rel_path


def _turns(output_path: Path) -> dict[str, float]:
    path = output_path / BACKUP_DIR / TURNS
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _record_turn(output_path: Path, rel_path: str, degrees: float | None) -> None:
    turns = _turns(output_path)
    if degrees is None:
        turns.pop(rel_path, None)
    else:
        turns[rel_path] = degrees
    path = output_path / BACKUP_DIR / TURNS
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{TURNS}.writing")
    tmp.write_text(json.dumps(turns, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def recovered_turn(backup: Path, current: Path) -> float | None:
    """How far a scan straightened before turns were recorded was turned, from its backup and the scan as it
    is now. That straightening grew the canvas to hold the turned scan, so only the turns (either way) that
    grow the backup to the scan's size can be it; of those, the one whose result looks most like the scan.
    None when no turn gives its size (it was straightened some other way)."""
    with Image.open(backup) as img:
        original = img.convert("L")
    with Image.open(current) as img:
        now = img.convert("L")
    steps = [round(k * 0.01, 2) for k in range(1, int(MAX_DEGREES * 100) + 1)]
    fits = [d for step in steps for d in (step, -step) if grown_size(original.size, d) == now.size]
    if not fits:
        return None
    # compared a few hundred pixels wide: enough to tell hundredths of a degree apart on a whole page
    shrink = max(1, original.width // 400)
    small_original = original.reduce(shrink)
    small = (max(1, now.width // shrink), max(1, now.height // shrink))
    now_small = np.asarray(now.resize(small, Image.Resampling.BILINEAR), dtype=np.float32)

    def unlike(degrees: float) -> float:
        turned = small_original.rotate(degrees, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=255)
        pixels = np.asarray(turned.resize(small, Image.Resampling.BILINEAR), dtype=np.float32)
        return float(np.abs(pixels - now_small).mean())

    return min(fits, key=unlike)


def _from_backup(output_path: Path, rel_path: str, degrees: float) -> None:
    """Rewrite an archived scan as its backup straightened ``degrees``, moving what its sidecar measures."""
    path = _archived(output_path, rel_path)
    _restore(_backup(output_path, rel_path), path)
    sidecar = read_sidecar(path)
    sizes = straighten_file(path, degrees)
    if sizes is not None and sidecar is not None:
        # What is measured on the scan moves with it, as Group's Straighten moves it: the page's trim, and
        # the boxes OCR found with the band they were read from.
        update: dict = {"trim": straightened_trim(sidecar.trim, degrees, *sizes)}
        if sidecar.ocr is not None:
            boxes, read = straighten_boxes(sidecar.ocr.boxes or [], degrees, *sizes, read=sidecar.ocr.trim)
            update["ocr"] = sidecar.ocr.model_copy(update={"boxes": boxes if sidecar.ocr.boxes is not None else None,
                                                           "trim": read})
        write_sidecar(path, sidecar.model_copy(update=update))
    _record_turn(output_path, rel_path, degrees)


def _turned_so_far(output_path: Path, rel_path: str) -> float:
    turn = _turns(output_path).get(rel_path)
    if turn is None:
        turn = recovered_turn(_backup(output_path, rel_path), _archived(output_path, rel_path))
    if turn is None:
        raise ValueError(f"can't tell how far {rel_path} was turned: Undo it, then straighten it again")
    return turn


def straighten(output_path: Path, rel_path: str, degrees: float) -> None:
    """Straighten one archived scan ``degrees`` further and move its OCR boxes with it, after backing both up
    (once: straightening a scan again keeps the backup of how it was first, and turns that by the total)."""
    if not degrees or not -MAX_DEGREES <= degrees <= MAX_DEGREES:
        raise ValueError(f"a turn of {degrees}° isn't one this straightens")
    path = _archived(output_path, rel_path)
    backup = _backup(output_path, rel_path)
    if backup.exists():
        degrees = round(_turned_so_far(output_path, rel_path) + degrees, 2)
        if abs(degrees) > MAX_DEGREES:
            raise ValueError(f"a turn of {degrees}° in all is beyond the {MAX_DEGREES}° this straightens")
    else:
        backup.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path = sidecar_path_for(path)
        if sidecar_path.is_file():
            shutil.copy2(sidecar_path, sidecar_path_for(backup))
        shutil.copy2(path, backup)
    _from_backup(output_path, rel_path, degrees)
    with _lock:
        _scan.found = [f for f in _scan.found if f.rel_path != rel_path]


class Redone(BaseModel):
    redone: list[str] = Field(default_factory=list)
    #: Scans whose turn couldn't be told (they stay as they are; Undo and straighten them again).
    skipped: list[str] = Field(default_factory=list)


def redo(output_path: Path) -> Redone:
    """Straighten every straightened scan again from its backup, by the turn it was given, the way scans are
    straightened now (cropped to the levelled page)."""
    result = Redone()
    for rel_path in straightened(output_path):
        try:
            _from_backup(output_path, rel_path, _turned_so_far(output_path, rel_path))
        except (KeyError, ValueError):
            result.skipped.append(rel_path)
        else:
            result.redone.append(rel_path)
    return result


def backup_of(output_path: Path, rel_path: str) -> Path:
    """A straightened scan's backup, as it was before straightening."""
    root = (output_path / BACKUP_DIR).resolve()
    path = (root / rel_path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise KeyError(f"no backup of {rel_path}")
    return path


def straightened(output_path: Path) -> list[str]:
    """The scans straightened here that can still be put back, as paths under the output folder."""
    root = output_path / BACKUP_DIR
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def _restore(backup: Path, path: Path) -> None:
    """Copy a backup and its sidecar over the archived scan and its sidecar, each swapped in whole."""
    for source, target in ((sidecar_path_for(backup), sidecar_path_for(path)), (backup, path)):
        if source.is_file():
            tmp = target.with_name(f"{target.name}.restoring")
            shutil.copy2(source, tmp)
            tmp.replace(target)


def undo(output_path: Path, rel_path: str) -> None:
    """Put a straightened scan and its sidecar back as they were, and drop the backup."""
    backup = _backup(output_path, rel_path)
    if not backup.is_file():
        raise KeyError(f"no backup of {rel_path}")
    path = _archived(output_path, rel_path)
    _restore(backup, path)
    sidecar_path_for(backup).unlink(missing_ok=True)
    backup.unlink()
    _record_turn(output_path, rel_path, None)
    estimate = estimate_file_skew(path)   # back as it was: offered again, if it still looks tilted
    batch_id = _batch_of(path)
    with _lock:
        if _listed(estimate.degrees, estimate.gain, estimate.within_range) \
                and all(f.rel_path != rel_path for f in _scan.found):
            _scan.found.append(Found(rel_path=rel_path, degrees=estimate.degrees, batch_id=batch_id))
            _sort(_scan.found)


def finalize(output_path: Path) -> int:
    """Delete every backup, once the straightened scans have been checked: they can't be undone after.
    Returns how many scans' backups went."""
    count = len(straightened(output_path))
    root = output_path / BACKUP_DIR
    if root.is_dir():
        shutil.rmtree(root)
    return count
