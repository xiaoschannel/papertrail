"""Mid-ingest files for the Review API: cached reads, serialized decision writes.

``ocr.json`` (with boxes) and ``extractions.json`` are large and only change when OCR or Parse runs, so
they are cached per file and reloaded when the file's mtime changes. ``decisions.json`` is small and is
also written by other pages (File Index, Normalize, Archive), so it is always read fresh, and every
read-modify-write goes through one lock so two requests can't lose each other's decision.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from data import load_extractions, load_ocr_results, load_smart_match_cache
from models import load_scan_index

T = TypeVar("T")

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[tuple[int, int], object]] = {}
_load_locks: dict[tuple[str, str], threading.Lock] = {}

#: Hold while reading, changing and saving decisions.json.
decisions_lock = threading.Lock()


def _signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (-1, -1)
    return (stat.st_mtime_ns, stat.st_size)


def _cached(output_path: Path, filename: str, loader: Callable[[Path], T]) -> T:
    """Load once per (mtime, size) of the file. Callers must not mutate the returned object."""
    path = output_path / filename
    key = (str(output_path.resolve()), filename)
    with _cache_lock:
        load_lock = _load_locks.setdefault(key, threading.Lock())
    # concurrent requests on a cold cache (the page fires several) wait for one load of a large file
    with load_lock:
        signature = _signature(path)
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and hit[0] == signature:
            return hit[1]  # type: ignore[return-value]
        value = loader(output_path)
        with _cache_lock:
            _cache[key] = (signature, value)
        return value


def scan_index(output_path: Path):
    return _cached(output_path, "batches.json", load_scan_index)


def ocr_results(output_path: Path):
    return _cached(output_path, "ocr.json", load_ocr_results)


def extractions(output_path: Path):
    return _cached(output_path, "extractions.json", load_extractions)


def smart_match_cache(output_path: Path):
    return _cached(output_path, "smart_match_cache.json", load_smart_match_cache)


def clear() -> None:
    with _cache_lock:
        _cache.clear()
