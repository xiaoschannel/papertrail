"""Mid-ingest files for the Review API: cached reads, serialized decision writes.

The OCR results (``ocr/<batch_id>.json``, with boxes) and ``extractions.json`` are large and only change
when OCR or Parse runs, so they are cached and reloaded when a file's mtime or size changes (for the OCR
folder: when any batch file does, or one comes or goes). ``decisions.json`` is small and is
also written by other pages (File Index, Normalize, Archive), so it is always read fresh, and every
read-modify-write goes through one lock so two requests can't lose each other's decision.

Which way up each scan faces is cached the same way, per scan file: Slice and Group ask for a whole batch's
on every visit, and a scan only changes when someone rotates it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from data import OCR_DIR, load_extractions, load_ocr_results, load_smart_match_cache, migrate_ocr_results
from models import load_scan_index
from orientation import OrientationEstimate, estimate_file_orientation

T = TypeVar("T")

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[object, object]] = {}
_load_locks: dict[tuple[str, str], threading.Lock] = {}

#: Hold while reading, changing and saving decisions.json.
decisions_lock = threading.Lock()


def _signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (-1, -1)
    return (stat.st_mtime_ns, stat.st_size)


def _ocr_signature(output_path: Path) -> tuple:
    """Every OCR batch file's signature. A legacy ocr.json is migrated first, so the load that follows
    doesn't change what was just measured."""
    migrate_ocr_results(output_path)
    folder = output_path / OCR_DIR
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    return tuple((p.name, _signature(p)) for p in files)


def _cached(output_path: Path, filename: str, loader: Callable[[Path], T],
            signature: Callable[[Path], object] | None = None) -> T:
    """Load once per (mtime, size) of the file. Callers must not mutate the returned object."""
    key = (str(output_path.resolve()), filename)
    with _cache_lock:
        load_lock = _load_locks.setdefault(key, threading.Lock())
    # concurrent requests on a cold cache (the page fires several) wait for one load of a large file
    with load_lock:
        seen = signature(output_path) if signature else _signature(output_path / filename)
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and hit[0] == seen:
            return hit[1]  # type: ignore[return-value]
        value = loader(output_path)
        with _cache_lock:
            _cache[key] = (seen, value)
        return value


def scan_index(output_path: Path):
    return _cached(output_path, "batches.json", load_scan_index)


def ocr_results(output_path: Path):
    return _cached(output_path, OCR_DIR, load_ocr_results, _ocr_signature)


def extractions(output_path: Path):
    return _cached(output_path, "extractions.json", load_extractions)


def smart_match_cache(output_path: Path):
    return _cached(output_path, "smart_match_cache.json", load_smart_match_cache)


_orientation_cache: dict[str, tuple[tuple[int, int], OrientationEstimate]] = {}


def scan_orientation(path: Path) -> OrientationEstimate:
    """Which way one scan faces, estimated again when the file changes."""
    key = str(path.resolve())
    signature = _signature(path)
    with _cache_lock:
        hit = _orientation_cache.get(key)
    if hit is not None and hit[0] == signature:
        return hit[1]
    value = estimate_file_orientation(path)
    with _cache_lock:
        _orientation_cache[key] = (signature, value)
    return value


def clear() -> None:
    with _cache_lock:
        _cache.clear()
        _orientation_cache.clear()
