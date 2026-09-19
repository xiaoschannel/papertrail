"""Pure archive/index integrity checks behind the Dev pages.

Used by Sanity Check and Index Audit (``api/routers/dev.py``) and directly
unit-testable.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from models import ScanIndex, iter_indexed_files


# --- batch coverage (sanity check) ----------------------------------------
def batch_coverage(scan_index: ScanIndex, organized: set[str]) -> list[dict]:
    """Per-batch view of how many of its files are present in the archive."""
    rows: list[dict] = []
    for batch in scan_index.batches:
        files_in_batch = set(batch.files.values())
        files_organized = files_in_batch & organized
        rows.append({
            "batch_id": batch.batch_id,
            "archived": batch.archived,
            "in_batch": len(files_in_batch),
            "organized": len(files_organized),
            "missing": sorted(files_in_batch - organized),
        })
    return rows


def check_archive_sidecars(output_path: Path) -> list[tuple[str, list[str], list[str]]]:
    """Detect data-file/sidecar mismatches per ``YYYY/MM`` folder.

    Returns ``(folder, missing_sidecar, extra_sidecar)`` tuples for folders where
    a data file has no sidecar or a sidecar has no data file.
    """
    accepted_by_folder: dict[str, set[str]] = {}
    sidecar_stems_by_folder: dict[str, set[str]] = {}

    for year_dir in output_path.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not (month_dir.name.isdigit() or month_dir.name == "undated"):
                continue
            folder_key = f"{year_dir.name}/{month_dir.name}"
            data_files: set[str] = set()
            sidecar_stems: set[str] = set()
            for p in month_dir.iterdir():
                if not p.is_file():
                    continue
                if p.suffix == ".json":
                    sidecar_stems.add(p.stem)
                else:
                    data_files.add(p.name)
            if data_files:
                accepted_by_folder[folder_key] = data_files
            if sidecar_stems:
                sidecar_stems_by_folder[folder_key] = sidecar_stems

    failures: list[tuple[str, list[str], list[str]]] = []
    for folder in sorted(set(accepted_by_folder) | set(sidecar_stems_by_folder)):
        data_stems = {Path(f).stem for f in accepted_by_folder.get(folder, set())}
        sidecars = sidecar_stems_by_folder.get(folder, set())
        missing_sidecar = sorted(data_stems - sidecars)
        extra_sidecar = sorted(sidecars - data_stems)
        if missing_sidecar or extra_sidecar:
            failures.append((folder, missing_sidecar, extra_sidecar))
    return failures


# --- index audit ----------------------------------------------------------
def count_duplicate_filenames(scan_index: ScanIndex) -> dict[str, int]:
    """Filenames appearing in more than one batch -> their total occurrence count."""
    all_filenames = [fn for _b, _s, fn in iter_indexed_files(scan_index, include_archived=True)]
    counts = Counter(all_filenames)
    return {fn: c for fn, c in counts.items() if c > 1}


def batch_statistics(scan_index: ScanIndex) -> dict:
    """Totals + per-batch breakdown with a running file total."""
    entries = iter_indexed_files(scan_index, include_archived=True)
    unique = {fn for _b, _s, fn in entries}
    per_batch = []
    running_total = 0
    for batch in scan_index.batches:
        running_total += len(batch.files)
        per_batch.append({
            "batch_id": batch.batch_id,
            "files": len(batch.files),
            "running_total": running_total,
            "archived": batch.archived,
            "start": batch.start_datetime,
            "end": batch.end_datetime,
        })
    return {
        "total_batches": len(scan_index.batches),
        "archived": sum(1 for b in scan_index.batches if b.archived),
        "non_archived": sum(1 for b in scan_index.batches if not b.archived),
        "total_entries": len(entries),
        "unique_filenames": len(unique),
        "lost_to_dedup": len(entries) - len(unique),
        "per_batch": per_batch,
    }


def disk_vs_index_delta(disk_files: set[str], indexed: set[str]) -> tuple[set[str], set[str]]:
    """Returns (indexed_but_not_on_disk, on_disk_but_not_indexed)."""
    return indexed - disk_files, disk_files - indexed
