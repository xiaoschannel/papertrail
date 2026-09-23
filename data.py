import json
import os
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from models import (
    DocumentExtraction,
    DocumentExtractionAdapter,
    DocumentGroups,
    DocumentIndex,
    DocumentKey,
    ModelRun,
    OcrResult,
    OtherResult,
    ReceiptResult,
    ReviewDecision,
    ScanIndex,
    Sidecar,
    Trim,
    SmartMatchHistoryRow,
)


def sidecar_path_for(file_path: Path) -> Path:
    return file_path.with_suffix(".json")


def read_sidecar(file_path: Path) -> Sidecar | None:
    sidecar_path = sidecar_path_for(file_path)
    if not sidecar_path.exists():
        return None
    return Sidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))


def write_sidecar(file_path: Path, entry: Sidecar):
    atomic_write_text(sidecar_path_for(file_path), entry.model_dump_json(indent=2, exclude_none=True))


def delete_sidecar(file_path: Path):
    sidecar_path_for(file_path).unlink(missing_ok=True)


def atomic_write_text(target: Path, text: str) -> None:
    """Write via a uniquely named sibling temp file swapped into place.

    A crash mid-write can't leave a truncated file, and concurrent writers (API requests, background
    jobs) never share a temp file. On Windows the swap fails while another process
    has the target open for reading; readers are brief, so retry for a moment before giving up.
    """
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.stem}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())     # on disk before the swap, so a power cut can't leave the new name empty
        for attempt in range(20):
            try:
                tmp.replace(target)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


#: OCR results live one file per batch, ``ocr/<batch_id>.json``. Jobs and edits claim whole batches, so
#: only the one holding a batch ever writes its file: the batch claim is the lock, and a run on one batch
#: can't write back a stale copy of another batch's results.
OCR_DIR = "ocr"
#: The single file OCR results were kept in before; migrated into ``ocr/`` on first read and kept, renamed,
#: until the next Archive as a backup.
LEGACY_OCR, MIGRATED_OCR = "ocr.json", "ocr.json.migrated"

_ocr_migration_lock = threading.Lock()


def _ocr_batch_path(output_path: Path, batch_id: int) -> Path:
    return output_path / OCR_DIR / f"{batch_id}.json"


def _ocr_batch_of(key: str) -> int | None:
    left, _, right = key.partition(":")
    return int(left) if left.isdigit() and right else None


def _parse_ocr(text: str) -> dict[str, OcrResult]:
    data = json.loads(text)
    if "results" in data:       # an older layout that is no longer read
        return {}
    return {k: OcrResult.model_validate(v) for k, v in data.items()}


def _write_ocr_batch(output_path: Path, batch_id: int, results: dict[str, OcrResult]) -> None:
    path = _ocr_batch_path(output_path, batch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({k: v.model_dump() for k, v in results.items()}, indent=2, ensure_ascii=False))


def migrate_ocr_results(output_path: Path) -> None:
    """Split a legacy ``ocr.json`` into per-batch files, then rename it to ``ocr.json.migrated``.

    Safe to repeat: batch files are written whole (atomically), so an interrupted migration just runs again
    and writes the ones still missing. A batch that already has a file keeps it, so an ``ocr.json`` that
    turns up again (older code run against the folder) can't overwrite newer results.
    """
    legacy = output_path / LEGACY_OCR
    with _ocr_migration_lock:
        if not legacy.exists():
            return
        by_batch: dict[int, dict[str, OcrResult]] = {}
        for key, result in _parse_ocr(legacy.read_text(encoding="utf-8")).items():
            batch_id = _ocr_batch_of(key)
            if batch_id is not None:
                by_batch.setdefault(batch_id, {})[key] = result
        for batch_id, results in by_batch.items():
            if not _ocr_batch_path(output_path, batch_id).exists():
                _write_ocr_batch(output_path, batch_id, results)
        legacy.replace(output_path / MIGRATED_OCR)


def ocr_batch_ids(output_path: Path) -> list[int]:
    folder = output_path / OCR_DIR
    if not folder.is_dir():
        return []
    return sorted(int(p.stem) for p in folder.glob("*.json") if p.stem.isdigit())


def load_ocr_batch(output_path: Path, batch_id: int) -> dict[str, OcrResult]:
    """One batch's OCR results."""
    migrate_ocr_results(output_path)
    path = _ocr_batch_path(output_path, batch_id)
    return _parse_ocr(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_ocr_batch(output_path: Path, batch_id: int, results: dict[str, OcrResult]) -> None:
    """Replace one batch's OCR results. Only whoever holds the batch may call this."""
    if any(_ocr_batch_of(key) != batch_id for key in results):
        raise ValueError(f"OCR results for another batch can't be saved into batch {batch_id}")
    if results:
        _write_ocr_batch(output_path, batch_id, results)
    else:
        _ocr_batch_path(output_path, batch_id).unlink(missing_ok=True)


def load_ocr_results(output_path: Path) -> dict[str, OcrResult]:
    """Every batch's OCR results, for readers."""
    migrate_ocr_results(output_path)
    results: dict[str, OcrResult] = {}
    for batch_id in ocr_batch_ids(output_path):
        results.update(load_ocr_batch(output_path, batch_id))
    return results


def save_ocr_results(output_path: Path, results: dict[str, OcrResult]):
    """Replace every batch's OCR results (a batch left out loses its file). For tools and tests: a
    running step writes only the batches it holds, with ``save_ocr_batch``."""
    migrate_ocr_results(output_path)
    by_batch: dict[int, dict[str, OcrResult]] = {}
    for key, result in results.items():
        batch_id = _ocr_batch_of(key)
        if batch_id is None:
            raise ValueError(f"not a page key: {key}")
        by_batch.setdefault(batch_id, {})[key] = result
    for batch_id in set(ocr_batch_ids(output_path)) - set(by_batch):
        _ocr_batch_path(output_path, batch_id).unlink()
    for batch_id, batch_results in by_batch.items():
        save_ocr_batch(output_path, batch_id, batch_results)


def load_extractions(output_path: Path) -> dict[str, DocumentExtraction]:
    ext_file = output_path / "extractions.json"
    if not ext_file.exists():
        return {}
    data = json.loads(ext_file.read_text(encoding="utf-8"))
    return {k: DocumentExtractionAdapter.validate_python(v) for k, v in data.items()}


def save_extractions(output_path: Path, extractions: dict[str, DocumentExtraction]):
    d = {k: v.model_dump() for k, v in extractions.items()}
    atomic_write_text(output_path / "extractions.json", json.dumps(d, indent=2, ensure_ascii=False))


#: Held for every read-modify-write of extractions.json. Parse writes it while other steps (regrouping a
#: batch) change it too, so each writer re-reads the file under this lock rather than saving a copy it
#: loaded earlier.
extractions_lock = threading.Lock()


def merge_extractions(output_path: Path, produced: dict[str, DocumentExtraction]) -> None:
    """Write ``produced`` into extractions.json as it is on disk now, leaving every other key alone."""
    if not produced:
        return
    with extractions_lock:
        current = load_extractions(output_path)
        current.update(produced)
        save_extractions(output_path, current)


def drop_extractions(output_path: Path, keys: set[str]) -> int:
    """Remove these documents' extractions from the file as it is on disk now; returns how many went."""
    with extractions_lock:
        current = load_extractions(output_path)
        gone = keys & set(current)
        if gone:
            save_extractions(output_path, {k: v for k, v in current.items() if k not in gone})
        return len(gone)


#: Where the runs behind the mid-ingest results live, keyed exactly as the results they describe: pages
#: for OCR, documents for extractions. Archive folds them into the sidecars and deletes them.
OCR_RUNS, EXTRACTION_RUNS = "ocr_runs.json", "extraction_runs.json"

_runs_lock = threading.Lock()


def load_model_runs(output_path: Path, name: str) -> dict[str, ModelRun]:
    path = output_path / name
    if not path.exists():
        return {}
    return {k: ModelRun.model_validate(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def merge_model_runs(output_path: Path, name: str, produced: dict[str, ModelRun]) -> None:
    """Write ``produced`` into the file as it is on disk now, leaving every other key alone."""
    if not produced:
        return
    with _runs_lock:
        current = load_model_runs(output_path, name)
        current.update(produced)
        atomic_write_text(output_path / name,
                          json.dumps({k: v.model_dump() for k, v in current.items()}, indent=2, ensure_ascii=False))


def drop_model_runs(output_path: Path, name: str, keys: set[str]) -> int:
    """Remove these items' runs from the file as it is on disk now; returns how many went."""
    with _runs_lock:
        current = load_model_runs(output_path, name)
        gone = keys & set(current)
        if gone:
            atomic_write_text(output_path / name, json.dumps(
                {k: v.model_dump() for k, v in current.items() if k not in gone}, indent=2, ensure_ascii=False))
        return len(gone)


#: How pages still being ingested are trimmed, by page key; Archive folds each into its page's sidecar and
#: deletes the file. A page that isn't trimmed has no entry.
TRIMS = "trims.json"

_trims_lock = threading.Lock()


def load_trims(output_path: Path) -> dict[str, Trim]:
    path = output_path / TRIMS
    if not path.exists():
        return {}
    return {k: Trim.model_validate(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def set_trim(output_path: Path, key: str, band: Trim | None) -> None:
    """Trim one page (``None`` keeps the whole page), leaving every other page's trim alone."""
    with _trims_lock:
        current = load_trims(output_path)
        if band is None:
            if current.pop(key, None) is None:
                return
        else:
            current[key] = band
        _save_trims(output_path, current)


def drop_trims(output_path: Path, keys: set[str]) -> int:
    """Remove these pages' trims; returns how many went."""
    with _trims_lock:
        current = load_trims(output_path)
        gone = keys & set(current)
        if gone:
            _save_trims(output_path, {k: v for k, v in current.items() if k not in gone})
        return len(gone)


def _save_trims(output_path: Path, trims: dict[str, Trim]) -> None:
    atomic_write_text(output_path / TRIMS, json.dumps({k: v.model_dump() for k, v in sorted(trims.items())}, indent=2))


def load_decisions(output_path: Path) -> dict[str, ReviewDecision]:
    dec_file = output_path / "decisions.json"
    if not dec_file.exists():
        return {}
    raw = json.loads(dec_file.read_text(encoding="utf-8"))
    return {k: ReviewDecision(**v) for k, v in raw.items()}


def save_decisions(output_path: Path, decisions: dict[str, ReviewDecision]):
    d = {k: v.model_dump(exclude_none=True) for k, v in decisions.items()}
    atomic_write_text(output_path / "decisions.json", json.dumps(d, indent=2, ensure_ascii=False))


def load_smart_match_cache(output_path: Path) -> dict[str, dict]:
    cache_file = output_path / "smart_match_cache.json"
    if not cache_file.exists():
        return {}
    return json.loads(cache_file.read_text(encoding="utf-8"))


def save_smart_match_cache(output_path: Path, cache: dict[str, dict]):
    atomic_write_text(output_path / "smart_match_cache.json", json.dumps(cache, indent=2, ensure_ascii=False))


def build_smart_match_history(
    extractions: dict[str, DocumentExtraction],
    decisions: dict[str, ReviewDecision],
    smart_cache: dict[str, dict],
) -> list[SmartMatchHistoryRow]:
    rows: list[SmartMatchHistoryRow] = []
    for doc_key, decision in decisions.items():
        if decision.verdict != "accepted" or doc_key not in extractions:
            continue
        ext = extractions[doc_key]
        if isinstance(ext, ReceiptResult):
            rows.append(
                SmartMatchHistoryRow(
                    extracted=ext.name,
                    extracted_phone=ext.phone,
                    confirmed=decision.name,
                )
            )
        elif isinstance(ext, OtherResult):
            rows.append(
                SmartMatchHistoryRow(
                    extracted=ext.title,
                    extracted_phone="",
                    confirmed=decision.name,
                )
            )
    for doc_key, entry in smart_cache.items():
        if (
            doc_key in extractions
            and doc_key in decisions
            and decisions[doc_key].verdict == "accepted"
        ):
            continue
        ex = entry.get("extracted", "")
        conf = entry.get("confirmed", "")
        if not conf:
            continue
        rows.append(
            SmartMatchHistoryRow(
                extracted=ex,
                extracted_phone=entry.get("extracted_phone", ""),
                confirmed=conf,
            )
        )
    return rows


def load_name_normalizations(output_path: Path) -> dict[str, str]:
    f = output_path / "name_normalizations.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def save_name_normalizations(output_path: Path, normalizations: dict[str, str]):
    atomic_write_text(output_path / "name_normalizations.json",
                       json.dumps(normalizations, indent=2, ensure_ascii=False))


def load_kept_duplicates(output_path: Path) -> set[frozenset[str]]:
    """Document pairs you have said are NOT duplicates, so Dedupe stops offering them."""
    f = output_path / "not_duplicates.json"
    if not f.exists():
        return set()
    return {frozenset(pair) for pair in json.loads(f.read_text(encoding="utf-8"))}


def save_kept_duplicates(output_path: Path, pairs: set[frozenset[str]]):
    serializable = sorted(sorted(pair) for pair in pairs)
    atomic_write_text(output_path / "not_duplicates.json",
                      json.dumps(serializable, indent=2, ensure_ascii=False))


def load_distinct_pairs(output_path: Path) -> set[frozenset[str]]:
    f = output_path / "distinct_pairs.json"
    if not f.exists():
        return set()
    return {frozenset(pair) for pair in json.loads(f.read_text(encoding="utf-8"))}


def save_distinct_pairs(output_path: Path, pairs: set[frozenset[str]]):
    serializable = [sorted(pair) for pair in pairs]
    serializable.sort()
    atomic_write_text(output_path / "distinct_pairs.json",
                       json.dumps(serializable, indent=2, ensure_ascii=False))


def load_document_groups(output_path: Path) -> DocumentGroups:
    f = output_path / "documents.json"
    if not f.exists():
        return DocumentGroups(groups=[])
    return DocumentGroups.model_validate_json(f.read_text(encoding="utf-8"))


def save_document_groups(output_path: Path, doc_groups: DocumentGroups):
    atomic_write_text(output_path / "documents.json", doc_groups.model_dump_json(indent=2))


def save_scan_index(output_path: Path, index: ScanIndex):
    output_path.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path / "batches.json", index.model_dump_json(indent=2))


def build_document_index(
    output_path: Path,
    indexed_keys: set[str],
    ocr_keys: set[str] | None = None,
) -> DocumentIndex:
    raw = load_document_groups(output_path).groups
    return DocumentIndex.from_raw_groups(raw, indexed_keys, ocr_keys)


def _batch_id_from_key(key: str) -> int | None:
    doc_key = DocumentKey.parse(key)
    return doc_key.batch_id if doc_key else None


def replace_groups_for_batch(output_path: Path, batch_id: int, new_groups: list[list[str]]):
    doc = load_document_groups(output_path)
    multi = [g for g in new_groups if len(g) > 1]
    keep = [g for g in doc.groups if not (g and _batch_id_from_key(g[0]) == batch_id)]
    doc.groups = keep + multi
    save_document_groups(output_path, doc)


def clear_extractions_decisions_for_batch(output_path: Path, batch_id: int):
    with extractions_lock:   # a Parse on another batch may be merging into this file right now
        extractions = load_extractions(output_path)
        decisions = load_decisions(output_path)
        to_remove = {k for k in extractions if _batch_id_from_key(k) == batch_id}
        to_remove |= {k for k in decisions if _batch_id_from_key(k) == batch_id}
        for k in to_remove:
            extractions.pop(k, None)
            if k in decisions and decisions[k].verdict != "tossed":  # tosses survive regrouping
                decisions.pop(k)
        if to_remove:
            save_extractions(output_path, extractions)
            save_decisions(output_path, decisions)


def _iter_year_month_dirs(output_path: Path):
    for year_dir in output_path.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not (month_dir.name.isdigit() or month_dir.name == "undated"):
                continue
            yield month_dir


def _filed_scan_name(page: Path) -> str:
    """The scan a page in tossed/ or marked/ was filed from: its sidecar's name for it (a crop's includes its
    slices/ folder, which the file in tossed/ doesn't), or the file's own name without a sidecar."""
    sidecar = read_sidecar(page)
    return sidecar.original_filename if sidecar and sidecar.original_filename else page.name


def scan_organized_filenames(output_path: Path) -> set[str]:
    organized: set[str] = set()
    for folder in (output_path / "tossed", output_path / "marked"):
        if folder.exists():
            organized.update(_filed_scan_name(p) for p in folder.iterdir()
                             if p.is_file() and p.suffix.lower() != ".json")
    for month_dir in _iter_year_month_dirs(output_path):
        for sidecar_path in month_dir.glob("*.json"):
            sidecar = Sidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
            organized.add(sidecar.original_filename)
    return organized


def filed_scan_pages(output_path: Path) -> dict[str, Path]:
    """Each scan the archive holds a page of, image and all, and that page: what the scan folder no
    longer needs, once the page is in the folder's history.

    Stricter than ``scan_organized_filenames``, which takes a sidecar's word for it: here the page's image
    has to be there too, since it is the copy the scan is disposed of for.
    """
    filed: dict[str, Path] = {}
    for folder in (output_path / "tossed", output_path / "marked"):
        if folder.exists():
            for p in folder.iterdir():
                if p.is_file() and p.suffix.lower() != ".json":
                    filed[_filed_scan_name(p)] = p
    for month_dir in _iter_year_month_dirs(output_path):
        images = {p.stem: p for p in month_dir.iterdir() if p.is_file() and p.suffix.lower() != ".json"}
        for sidecar_path in month_dir.glob("*.json"):
            if sidecar_path.stem in images:
                sidecar = Sidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
                filed[sidecar.original_filename] = images[sidecar_path.stem]
    return filed


def load_reorganized_state(
    output_path: Path,
) -> tuple[set[str], dict[str, tuple[Sidecar, str]]]:
    tossed: set[str] = set()
    tossed_dir = output_path / "tossed"
    if tossed_dir.exists():
        tossed = {p.name for p in tossed_dir.iterdir() if p.is_file() and p.suffix.lower() != ".json"}

    accepted_metadata: dict[str, tuple[Sidecar, str]] = {}
    for month_dir in _iter_year_month_dirs(output_path):
        sidecar_paths: list[Path] = []
        stem_to_datafile: dict[str, Path] = {}
        for p in month_dir.iterdir():
            if not p.is_file():
                continue
            if p.suffix == ".json":
                sidecar_paths.append(p)
            else:
                stem_to_datafile[p.stem] = p
        for sidecar_path in sidecar_paths:
            sidecar = Sidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
            data_file = stem_to_datafile.get(sidecar_path.stem)
            rel_path = data_file.relative_to(output_path).as_posix() if data_file else ""
            accepted_metadata[sidecar.original_filename] = (sidecar, rel_path)

    return tossed, accepted_metadata


def load_embeddings_cache(output_path: Path) -> tuple[list[str], np.ndarray | None]:
    f = output_path / "name_embeddings.npz"
    if not f.exists():
        return [], None
    data = np.load(f, allow_pickle=False)
    return data["names"].tolist(), data["matrix"]


def save_embeddings_cache(output_path: Path, names: list[str], matrix: np.ndarray):
    np.savez_compressed(
        output_path / "name_embeddings.npz",
        names=np.array(names),
        matrix=matrix,
    )
