import json
import shutil
from collections import defaultdict
from pathlib import Path

from models import ReviewDecision

FULLWIDTH_COLON = "\uff1a"
WINDOWS_FORBIDDEN = str.maketrans({
    "<": "＜", ">": "＞", ":": "：", '"': "＂",
    "/": "／", "\\": "＼", "|": "｜", "?": "？", "*": "＊",
})


def sanitize_filename(name: str) -> str:
    return name.translate(WINDOWS_FORBIDDEN).rstrip(". ")


def parse_scan_datetime(fn: str) -> tuple[int, int, int, int, int, int]:
    ts_str = Path(fn).stem.split("_")[0]
    month, day, year = int(ts_str[:2]), int(ts_str[2:4]), int(ts_str[4:8])
    hour, minute, second = int(ts_str[8:10]), int(ts_str[10:12]), int(ts_str[12:14])
    return year, month, day, hour, minute, second


def build_accepted_name(dec: ReviewDecision, fn: str) -> tuple[str, str, int]:
    if dec.date:
        parts = dec.date.split("-")
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        time_parts = dec.time.split(":") if dec.time else ["0", "0"]
        hour = time_parts[0].zfill(2)
        minute = time_parts[1].zfill(2) if len(time_parts) > 1 else "00"
        seconds = int(time_parts[2]) if len(time_parts) > 2 else -1
        folder = f"{year}/{month:02d}"
        safe_name = sanitize_filename(dec.name)
        base = f"{year}年{month}月{day}日 {hour}{FULLWIDTH_COLON}{minute} {safe_name}"
    else:
        sy, sm, sd, sh, smi, ss = parse_scan_datetime(fn)
        seconds = ss
        folder = f"{sy}/undated"
        safe_name = sanitize_filename(dec.name)
        base = f"{sy}年{sm}月{sd}日 {sh:02d}{FULLWIDTH_COLON}{smi:02d} {safe_name}"
    return folder, base, seconds


def _stem_matches_base(stem: str, base: str) -> bool:
    if stem == base:
        return True
    if stem.startswith(f"{base} (") and stem.endswith(")"):
        return stem[len(f"{base} ("):-1].isdigit()
    return False


def plan_accepted_destinations(
    records: dict[str, ReviewDecision],
    existing_names_by_folder: dict[str, set[str]],
    filename_to_batch: dict[str, int] | None = None,
    filename_to_serial: dict[str, int] | None = None,
) -> dict[str, str]:
    if filename_to_batch is None:
        filename_to_batch = {}
    if filename_to_serial is None:
        filename_to_serial = {}

    groups: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for fn, dec in records.items():
        folder, base, seconds = build_accepted_name(dec, fn)
        groups[(folder, base)].append((fn, seconds))

    file_destinations: dict[str, str] = {}
    for (folder, base), members in groups.items():
        members.sort(key=lambda m: (
            m[1] if m[1] >= 0 else 9999,
            filename_to_batch.get(m[0], 0),
            filename_to_serial.get(m[0], 0),
        ))

        taken = existing_names_by_folder.get(folder, set())
        existing_count = 0
        if base in taken:
            existing_count = 1
        i = 2
        while f"{base} ({i})" in taken:
            existing_count = i
            i += 1

        start_idx = existing_count + 1 if existing_count > 0 else 0
        needs_suffix = len(members) > 1 or existing_count > 0

        for idx, (fn, _sec) in enumerate(members):
            ext = Path(fn).suffix
            if needs_suffix:
                suffix_num = start_idx + idx if existing_count > 0 else idx + 1
                if existing_count == 0 and idx == 0:
                    new_name = f"{base}{ext}"
                else:
                    new_name = f"{base} ({suffix_num}){ext}"
            else:
                new_name = f"{base}{ext}"
            file_destinations[fn] = f"{folder}/{new_name}"

    return file_destinations


def apply_reorganize(output_path: Path) -> list[tuple[str, str, str]]:
    accepted_metadata: dict[str, dict] = {}
    for year_dir in output_path.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not (month_dir.name.isdigit() or month_dir.name == "undated"):
                continue
            sidecars: list[Path] = []
            stem_to_datafile: dict[str, Path] = {}
            for p in month_dir.iterdir():
                if not p.is_file():
                    continue
                if p.suffix == ".json":
                    sidecars.append(p)
                else:
                    stem_to_datafile[p.stem] = p
            for sidecar in sidecars:
                entry = json.loads(sidecar.read_text(encoding="utf-8"))
                orig = entry.get("original_filename", "")
                if not orig:
                    continue
                data_file = stem_to_datafile.get(sidecar.stem)
                if data_file:
                    entry["_path"] = data_file.relative_to(output_path).as_posix()
                accepted_metadata[orig] = entry

    stale: dict[str, ReviewDecision] = {}
    stable_stems: dict[str, set[str]] = defaultdict(set)
    filename_to_batch: dict[str, int] = {}
    filename_to_serial: dict[str, int] = {}

    for fn, meta in accepted_metadata.items():
        review = meta.get("review", {})
        if not review:
            continue
        dec = ReviewDecision(**review)
        expected_folder, expected_base, _ = build_accepted_name(dec, fn)

        batch_id = meta.get("batch_id")
        serial = meta.get("serial")
        if batch_id is not None:
            filename_to_batch[fn] = batch_id
        if serial is not None:
            filename_to_serial[fn] = serial

        current_path = meta.get("_path", "")
        current_folder = str(Path(current_path).parent).replace("\\", "/") if current_path else ""
        current_stem = Path(current_path).stem if current_path else ""

        if current_folder == expected_folder and _stem_matches_base(current_stem, expected_base):
            stable_stems[expected_folder].add(current_stem)
        else:
            stale[fn] = dec

    if not stale:
        return []

    destinations = plan_accepted_destinations(
        stale, stable_stems, filename_to_batch, filename_to_serial,
    )

    moves: list[tuple[str, str, str]] = []
    for fn, new_dest in destinations.items():
        old_path_str = accepted_metadata[fn].get("_path", "")
        if old_path_str == new_dest:
            continue

        old_full = output_path / old_path_str if old_path_str else None
        new_full = output_path / new_dest

        if old_full and old_full.exists():
            new_full.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_full), str(new_full))
            old_sidecar = old_full.with_suffix(".json")
            if old_sidecar.exists():
                shutil.move(str(old_sidecar), str(new_full.with_suffix(".json")))

        moves.append((fn, old_path_str, new_dest))

    return moves


def scan_existing_names(output_path: Path) -> dict[str, set[str]]:
    existing_names_by_folder: dict[str, set[str]] = defaultdict(set)
    for year_dir in output_path.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not (month_dir.name.isdigit() or month_dir.name == "undated"):
                continue
            folder_key = f"{year_dir.name}/{month_dir.name}"
            for sidecar in month_dir.glob("*.json"):
                existing_names_by_folder[folder_key].add(sidecar.stem)
    return existing_names_by_folder


def resolve_single_accepted_destination(
    output_path: Path,
    fn: str,
    dec: ReviewDecision,
) -> str:
    folder, base, _seconds = build_accepted_name(dec, fn)
    ext = Path(fn).suffix

    taken: set[str] = set()
    target_dir = output_path / folder
    if target_dir.exists():
        for sidecar in target_dir.glob("*.json"):
            taken.add(sidecar.stem)

    if base not in taken:
        return f"{folder}/{base}{ext}"

    i = 2
    while f"{base} ({i})" in taken:
        i += 1
    return f"{folder}/{base} ({i}){ext}"
