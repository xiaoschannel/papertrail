import json
from pathlib import Path

from pydantic import BaseModel

from data import _iter_year_month_dirs
from models import DocumentExtractionAdapter, Sidecar, iter_indexed_files, load_scan_index
from settings import IMAGE_EXTENSIONS


def transform_receipt_extraction_dict(ext: dict) -> bool:
    if ext.get("document_type") != "receipt":
        return False
    changed = False
    if "location" in ext:
        loc = ext.pop("location")
        if not ext.get("address"):
            ext["address"] = loc
        changed = True
    fs = ext.get("field_sources")
    if isinstance(fs, dict) and "location" in fs:
        fs["address"] = fs.pop("location")
        changed = True
    return changed


def iter_sidecar_json_paths(output_path: Path) -> list[Path]:
    paths: list[Path] = []
    for month_dir in _iter_year_month_dirs(output_path):
        paths.extend(sorted(month_dir.glob("*.json")))
    return paths


def count_non_json_files_in_dir(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for p in d.iterdir() if p.is_file() and p.suffix.lower() != ".json")


def indexed_not_on_input_count(output_path: Path, input_path: Path | None) -> int | None:
    if input_path is None or not input_path.is_dir():
        return None
    bp = output_path / "batches.json"
    if not bp.is_file():
        return None
    scan_index = load_scan_index(output_path)
    unique_filenames = {fn for _, _, fn in iter_indexed_files(scan_index, include_archived=True)}
    disk_files = {f.name for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS}
    return len(unique_filenames - disk_files)


class MigrationStats(BaseModel):
    extractions_path: str | None = None
    extractions_changed: bool = False
    extractions_receipts_updated: int = 0
    total_sidecar_json: int = 0
    sidecars_no_change_needed: int = 0
    marked_non_json: int = 0
    tossed_non_json: int = 0
    output_total_for_compare: int = 0
    index_audit_not_on_input: int | None = None
    sidecar_paths: list[str] = []
    sidecars_updated: int = 0


def apply_reconciliation_stats(
    stats: MigrationStats, output_path: Path, input_path: Path | None
) -> None:
    stats.marked_non_json = count_non_json_files_in_dir(output_path / "marked")
    stats.tossed_non_json = count_non_json_files_in_dir(output_path / "tossed")
    stats.output_total_for_compare = (
        stats.total_sidecar_json + stats.marked_non_json + stats.tossed_non_json
    )
    stats.index_audit_not_on_input = indexed_not_on_input_count(output_path, input_path)


def collect_migration_preview(output_path: Path, input_path: Path | None = None) -> MigrationStats:
    stats = MigrationStats()
    ext_path = output_path / "extractions.json"
    if ext_path.exists():
        stats.extractions_path = str(ext_path)
        raw = json.loads(ext_path.read_text(encoding="utf-8"))
        n = 0
        for _k, v in raw.items():
            if isinstance(v, dict):
                v_copy = json.loads(json.dumps(v))
                if transform_receipt_extraction_dict(v_copy):
                    n += 1
        if n:
            stats.extractions_changed = True
            stats.extractions_receipts_updated = n

    all_sidecars = iter_sidecar_json_paths(output_path)
    stats.total_sidecar_json = len(all_sidecars)
    for p in all_sidecars:
        raw = json.loads(p.read_text(encoding="utf-8"))
        ext = raw.get("extraction")
        if not isinstance(ext, dict):
            continue
        ext_copy = json.loads(json.dumps(ext))
        if transform_receipt_extraction_dict(ext_copy):
            stats.sidecar_paths.append(str(p.relative_to(output_path)))

    stats.sidecars_no_change_needed = stats.total_sidecar_json - len(stats.sidecar_paths)

    apply_reconciliation_stats(stats, output_path, input_path)

    return stats


def migrate_extractions_json(output_path: Path) -> tuple[bool, int]:
    path = output_path / "extractions.json"
    if not path.exists():
        return False, 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for _k, v in raw.items():
        if isinstance(v, dict) and transform_receipt_extraction_dict(v):
            n += 1
            DocumentExtractionAdapter.validate_python(v)
    if n == 0:
        return False, 0
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return True, n


def migrate_sidecar_json(path: Path) -> bool:
    raw = json.loads(path.read_text(encoding="utf-8"))
    ext = raw.get("extraction")
    if not isinstance(ext, dict):
        return False
    if not transform_receipt_extraction_dict(ext):
        return False
    Sidecar.model_validate(raw)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def run_migration_apply(output_path: Path, input_path: Path | None = None) -> MigrationStats:
    stats = MigrationStats()
    ext_path = output_path / "extractions.json"
    if ext_path.exists():
        stats.extractions_path = str(ext_path)
    ch, n = migrate_extractions_json(output_path)
    stats.extractions_changed = ch
    stats.extractions_receipts_updated = n

    all_sidecars = iter_sidecar_json_paths(output_path)
    stats.total_sidecar_json = len(all_sidecars)
    for p in all_sidecars:
        if migrate_sidecar_json(p):
            stats.sidecars_updated += 1
            stats.sidecar_paths.append(str(p.relative_to(output_path)))

    stats.sidecars_no_change_needed = stats.total_sidecar_json - stats.sidecars_updated

    apply_reconciliation_stats(stats, output_path, input_path)

    return stats
