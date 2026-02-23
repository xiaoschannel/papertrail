from datetime import datetime
from pathlib import Path
from typing import Callable

from models import ScanBatch

IndexingScheme = Callable[[list[str]], tuple[list[ScanBatch], list[str], list[str]]]


def parse_canon_filename(name: str) -> tuple[datetime, int] | None:
    stem = Path(name).stem
    parts = stem.split("_")
    if len(parts) != 2 or len(parts[0]) != 14 or not parts[1].isdigit():
        return None
    ts_str = parts[0]
    month, day, year = int(ts_str[:2]), int(ts_str[2:4]), int(ts_str[4:8])
    hour, minute, second = int(ts_str[8:10]), int(ts_str[10:12]), int(ts_str[12:14])
    dt = datetime(year, month, day, hour, minute, second)
    serial = int(parts[1])
    return dt, serial


def canon_imageformula(filenames: list[str]) -> tuple[list[ScanBatch], list[str], list[str]]:
    parsed: list[tuple[datetime, int, str]] = []
    skipped: list[str] = []
    for fn in filenames:
        result = parse_canon_filename(fn)
        if result:
            parsed.append((result[0], result[1], fn))
        else:
            skipped.append(fn)
    parsed.sort(key=lambda x: (x[0], x[1]))
    batches: list[ScanBatch] = []
    warnings: list[str] = []
    current_files: dict[int, str] = {}
    current_datetimes: list[datetime] = []
    next_id = 1
    prev_serial: int | None = None

    for dt, serial, filename in parsed:
        if prev_serial is not None and serial <= prev_serial:
            batches.append(ScanBatch(
                batch_id=next_id,
                start_datetime=min(current_datetimes).strftime("%Y-%m-%d %H:%M:%S"),
                end_datetime=max(current_datetimes).strftime("%Y-%m-%d %H:%M:%S"),
                files=current_files,
            ))
            next_id += 1
            current_files = {}
            current_datetimes = []
        elif prev_serial is not None and serial > prev_serial + 1:
            warnings.append(f"Batch {next_id}: serial skip {prev_serial} -> {serial} (at {filename})")
        current_files[serial] = filename
        current_datetimes.append(dt)
        prev_serial = serial

    if current_files:
        batches.append(ScanBatch(
            batch_id=next_id,
            start_datetime=min(current_datetimes).strftime("%Y-%m-%d %H:%M:%S"),
            end_datetime=max(current_datetimes).strftime("%Y-%m-%d %H:%M:%S"),
            files=current_files,
        ))
    return batches, skipped, warnings


def single_batch_by_filename(filenames: list[str]) -> tuple[list[ScanBatch], list[str], list[str]]:
    ordered = sorted(filenames)
    files = {i + 1: fn for i, fn in enumerate(ordered)}
    batch = ScanBatch(
        batch_id=1,
        start_datetime="N/A",
        end_datetime="N/A",
        files=files,
    )
    return [batch], [], []


SCHEMES: dict[str, IndexingScheme] = {
    "Canon ImageFormula": canon_imageformula,
    "Single batch (by filename)": single_batch_by_filename,
}
