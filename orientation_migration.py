"""One-off migration: find archived scans that are sideways or upside down, and turn the ones confirmed.

Scans filed before Slice and Group pointed turned pages out may still be turned. This looks at every
scan under the output folder (filed documents, marked/ and tossed/) with the orientation model and lists
the ones that look turned; the Turn Archive dev page shows them, and each is turned only when someone
confirms it. Unarchived batches aren't looked at here: Slice and Group point theirs out.

The archived scan is the only copy, so before one is turned it and its sidecar are copied under
``.orientation-backup/`` in the output folder, and Undo puts both back. Turning rewrites the scan (as
the rotate arrows do) and turns the OCR boxes in its sidecar with it, so field boxes still land on
their text; box numbering is kept, so the extraction's field sources stay valid.

Temporary: kept as a git tag, not part of the shipped feature.
"""

from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from data import _iter_year_month_dirs, read_sidecar, sidecar_path_for, write_sidecar
from document_grouping import ROTATIONS, rotate_file_upright
from orientation import UNREADABLE, estimate_file_orientation, load_model
from settings import IMAGE_EXTENSIONS

BACKUP_DIR = ".orientation-backup"
_SCALE = 1000


def archived_scans(output_path: Path) -> list[Path]:
    """Every scan under the output folder: filed documents, then marked/ and tossed/."""
    folders = sorted(_iter_year_month_dirs(output_path)) + [output_path / "marked", output_path / "tossed"]
    return [path for folder in folders if folder.is_dir() for path in sorted(folder.iterdir())
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.endswith(".enhanced.png")]


@dataclass
class Found:
    rel_path: str
    top_points: str
    confidence: float


@dataclass
class Scan:
    """How far a look through the archive has got, and what it has found so far."""
    running: bool = False
    done: int = 0
    total: int = 0
    unreadable: int = 0
    error: str | None = None
    found: list[Found] = field(default_factory=list)


_lock = threading.Lock()
_scan = Scan()


def scan_state() -> Scan:
    with _lock:
        return Scan(_scan.running, _scan.done, _scan.total, _scan.unreadable, _scan.error, list(_scan.found))


def start_scan(output_path: Path) -> bool:
    """Look through the archive in the background; False if a look is already under way."""
    global _scan
    with _lock:
        if _scan.running:
            return False
        _scan = Scan(running=True)
    threading.Thread(target=_run_scan, args=(output_path,), name="orientation-migration", daemon=True).start()
    return True


def _run_scan(output_path: Path) -> None:
    try:
        load_model()
        scans = archived_scans(output_path)
        with _lock:
            _scan.total = len(scans)

        def one(path: Path) -> None:
            try:
                estimate = estimate_file_orientation(path)
            except UNREADABLE:
                with _lock:
                    _scan.unreadable += 1
                    _scan.done += 1
                return
            with _lock:
                _scan.done += 1
                if estimate.needs_turning:
                    _scan.found.append(Found(path.relative_to(output_path).as_posix(), estimate.top_points,
                                             estimate.confidence))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(one, scans))
    except Exception as exc:   # shown on the page; the look can be started again
        with _lock:
            _scan.error = str(exc) or type(exc).__name__
    finally:
        with _lock:
            _scan.found.sort(key=lambda f: -f.confidence)
            _scan.running = False


def _archived(output_path: Path, rel_path: str) -> Path:
    root = output_path.resolve()
    path = (root / rel_path).resolve()
    if not path.is_relative_to(root) or path.is_relative_to(root / BACKUP_DIR) or not path.is_file():
        raise KeyError(f"no archived scan {rel_path}")
    return path


def _backup(output_path: Path, rel_path: str) -> Path:
    return output_path / BACKUP_DIR / rel_path


def turned_box(coords: list[int], top_points: str) -> list[int]:
    """A box measured on the scan, placed on the scan turned upright from ``top_points`` (0-1000 scale)."""
    if len(coords) != 4:
        return coords
    x1, y1, x2, y2 = coords
    if top_points == "left":     # turned 90° clockwise
        return [_SCALE - y2, x1, _SCALE - y1, x2]
    if top_points == "right":    # turned 90° counter-clockwise
        return [y1, _SCALE - x2, y2, _SCALE - x1]
    return [_SCALE - x2, _SCALE - y2, _SCALE - x1, _SCALE - y1]


def turn(output_path: Path, rel_path: str, top_points: str) -> None:
    """Turn one archived scan upright, and its OCR boxes with it, after backing both up (once: turning a
    scan again keeps the backup of how it was first)."""
    if top_points not in ROTATIONS:
        raise ValueError(f"not a rotate arrow: {top_points}")
    path = _archived(output_path, rel_path)
    backup = _backup(output_path, rel_path)
    sidecar_path = sidecar_path_for(path)
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        if sidecar_path.is_file():
            shutil.copy2(sidecar_path, sidecar_path_for(backup))
        shutil.copy2(path, backup)
    sidecar = read_sidecar(path)
    rotate_file_upright(path, top_points)
    if sidecar is not None and sidecar.ocr is not None and sidecar.ocr.boxes:
        for box in sidecar.ocr.boxes:
            box.coords = [turned_box(c, top_points) for c in box.coords]
        write_sidecar(path, sidecar)
    with _lock:
        _scan.found = [f for f in _scan.found if f.rel_path != rel_path]


def turned(output_path: Path) -> list[str]:
    """The scans turned here that can still be put back, as paths under the output folder."""
    root = output_path / BACKUP_DIR
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def undo(output_path: Path, rel_path: str) -> None:
    """Put a turned scan and its sidecar back as they were, and drop the backup."""
    backup = _backup(output_path, rel_path)
    if not backup.is_file():
        raise KeyError(f"no backup of {rel_path}")
    path = _archived(output_path, rel_path)
    for source, target in ((sidecar_path_for(backup), sidecar_path_for(path)), (backup, path)):
        if source.is_file():
            tmp = target.with_name(f"{target.name}.restoring")
            shutil.copy2(source, tmp)
            tmp.replace(target)
    sidecar_path_for(backup).unlink(missing_ok=True)
    backup.unlink()
    estimate = estimate_file_orientation(path)   # back as it was: offered again, if it still looks turned
    with _lock:
        if estimate.needs_turning and all(f.rel_path != rel_path for f in _scan.found):
            _scan.found.append(Found(rel_path, estimate.top_points, estimate.confidence))
            _scan.found.sort(key=lambda f: -f.confidence)
