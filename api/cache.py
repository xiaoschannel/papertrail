"""Server-side cache for the archive-derived visualization frames.

Every visualize endpoint needs ``build_viz_records()``, which parses every sidecar
in the archive (over a second on a large archive). Uncached, each page pays that per
request — a dashboard view fires several in parallel, and a merchant profile would
parse the archive twice (records, then items).

An entry is keyed on a cheap archive-structure signature (which includes the
brand-registry mtime) and expires after ``TTL_SECONDS``. Archiving, tossing and
reorganizing create/move/delete files, which bumps the containing directory's
mtime, so those changes show up on the next request. The TTL is the backstop
for in-place sidecar edits, which do not touch directory mtimes.

``viz_records`` itself stays uncached — caching is a serving concern, so it lives
here rather than in the pure module. Cached frames are shared between requests;
pandas 3 copy-on-write keeps callers from mutating them through filters/slices.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from brand_registry import brand_registry_mtime
from data import load_reorganized_state
from models import Sidecar
from viz_records import build_viz_records, viz_items_from_records

TTL_SECONDS = 120.0


@dataclass
class _Entry:
    signature: tuple
    built_at: float
    records: pd.DataFrame
    # tossed page filenames, and every archived page by its original filename -> (sidecar, path)
    state: tuple[set[str], dict[str, tuple[Sidecar, str]]]
    items: pd.DataFrame | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_registry_lock = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}
_entries: dict[str, _Entry] = {}


def _archive_signature(output_path: Path) -> tuple:
    """Brand-registry mtime + mtimes of the year, month, tossed and marked dirs.

    Stats only directories (~100 on a large archive), never the sidecars.
    """
    sig: list = [brand_registry_mtime()]
    try:
        for top in sorted(output_path.iterdir(), key=lambda p: p.name):
            if not top.is_dir():
                continue
            if top.name in ("tossed", "marked"):
                sig.append((top.name, top.stat().st_mtime_ns))
            elif top.name.isdigit():
                sig.append((top.name, top.stat().st_mtime_ns))
                for month in sorted(top.iterdir(), key=lambda p: p.name):
                    if month.is_dir():
                        sig.append((top.name, month.name, month.stat().st_mtime_ns))
    except FileNotFoundError:
        pass
    return tuple(sig)


def _entry(output_path: Path) -> _Entry:
    key = str(output_path.resolve())
    with _registry_lock:
        path_lock = _path_locks.setdefault(key, threading.Lock())
    # One build per archive at a time: concurrent requests on a cold cache wait
    # for the first build instead of each re-parsing the whole archive.
    with path_lock:
        signature = _archive_signature(output_path)
        now = time.monotonic()
        entry = _entries.get(key)
        if entry is not None and entry.signature == signature and now - entry.built_at < TTL_SECONDS:
            return entry
        state = load_reorganized_state(output_path)
        entry = _Entry(signature=signature, built_at=now, records=build_viz_records(output_path, state), state=state)
        _entries[key] = entry
        return entry


def viz_records(output_path: Path) -> pd.DataFrame:
    return _entry(output_path).records


def archive_state(output_path: Path) -> tuple[set[str], dict[str, tuple[Sidecar, str]]]:
    """Where every scan ended up: tossed filenames, and archived pages by original filename."""
    return _entry(output_path).state


def viz_items(output_path: Path) -> pd.DataFrame:
    entry = _entry(output_path)
    with entry.lock:
        if entry.items is None:
            entry.items = viz_items_from_records(entry.records)
        return entry.items


def clear() -> None:
    """Drop everything. Call after any write that changes archive-derived data."""
    with _registry_lock:
        _entries.clear()
