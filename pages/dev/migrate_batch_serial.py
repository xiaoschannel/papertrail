import json
import shutil
from pathlib import Path

import streamlit as st

from data import (
    save_decisions,
    save_extractions,
    save_name_cache,
    save_ocr_results,
)
from models import (
    DocumentExtractionAdapter,
    OcrBatch,
    OcrResult,
    ReviewDecision,
    batch_serial_key,
    filename_to_batch_serial,
    load_scan_index,
)
from settings import get_config

st.title("Migrate to Batch+Serial Keys")

st.markdown(
    "One-time migration from filename-keyed ingest artifacts to batch:serial keys. "
    "Requires batches.json. Backs up originals before overwriting."
)

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
batches_file = output_path / "batches.json"
if not batches_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
fn_to_bs = filename_to_batch_serial(scan_index)


def resolve_key(fn: str) -> str | None:
    if fn in fn_to_bs:
        return batch_serial_key(*fn_to_bs[fn])
    return None


ocr_file = output_path / "ocr.json"
ext_file = output_path / "extractions.json"
dec_file = output_path / "decisions.json"
name_cache_file = output_path / "name_cache.json"

def is_legacy_ocr(path: Path) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return "results" in data


def is_legacy_keyed(path: Path, filename_set: set[str]) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return any(k in filename_set for k in data)


indexed_filenames = set(fn_to_bs.keys())
ocr_old = is_legacy_ocr(ocr_file)
ext_old = is_legacy_keyed(ext_file, indexed_filenames)
dec_old = is_legacy_keyed(dec_file, indexed_filenames)
name_old = is_legacy_keyed(name_cache_file, indexed_filenames)

if not (ocr_old or ext_old or dec_old or name_old):
    st.success("No legacy artifacts to migrate.")
    st.stop()

if ocr_old:
    st.subheader("OCR")
    ocr_data = json.loads(ocr_file.read_text(encoding="utf-8"))
    batch = OcrBatch.model_validate(ocr_data)
    migratable = []
    orphans = []
    for r in batch.results:
        k = resolve_key(r.filename)
        if k:
            migratable.append((k, r))
        else:
            orphans.append(r.filename)
    st.metric("Migratable", len(migratable))
    st.metric("Orphans", len(orphans))
    if orphans:
        with st.expander("Orphan files"):
            for fn in orphans[:20]:
                st.text(fn)
            if len(orphans) > 20:
                st.text(f"... and {len(orphans) - 20} more")

if ext_old:
    st.subheader("Extractions")
    ext_data = json.loads(ext_file.read_text(encoding="utf-8"))
    migratable = []
    orphans = []
    for fn, v in ext_data.items():
        k = resolve_key(fn)
        if k:
            migratable.append((k, DocumentExtractionAdapter.validate_python(v)))
        else:
            orphans.append(fn)
    st.metric("Migratable", len(migratable))
    st.metric("Orphans", len(orphans))
    if orphans:
        with st.expander("Orphan files"):
            for fn in orphans[:20]:
                st.text(fn)
            if len(orphans) > 20:
                st.text(f"... and {len(orphans) - 20} more")

if dec_old:
    st.subheader("Decisions")
    dec_data = json.loads(dec_file.read_text(encoding="utf-8"))
    migratable = []
    orphans = []
    for fn, v in dec_data.items():
        k = resolve_key(fn)
        if k:
            migratable.append((k, ReviewDecision(**v)))
        else:
            orphans.append(fn)
    st.metric("Migratable", len(migratable))
    st.metric("Orphans", len(orphans))
    if orphans:
        with st.expander("Orphan files"):
            for fn in orphans[:20]:
                st.text(fn)
            if len(orphans) > 20:
                st.text(f"... and {len(orphans) - 20} more")

if name_old:
    st.subheader("Name cache")
    name_data = json.loads(name_cache_file.read_text(encoding="utf-8"))
    migratable = []
    orphans = []
    for fn, v in name_data.items():
        k = resolve_key(fn)
        if k:
            migratable.append((k, v))
        else:
            orphans.append(fn)
    st.metric("Migratable", len(migratable))
    st.metric("Orphans", len(orphans))
    if orphans:
        with st.expander("Orphan files"):
            for fn in orphans[:20]:
                st.text(fn)
            if len(orphans) > 20:
                st.text(f"... and {len(orphans) - 20} more")

if st.button("Run Migration", width="stretch", type="primary"):
    if ocr_old:
        ocr_data = json.loads(ocr_file.read_text(encoding="utf-8"))
        batch = OcrBatch.model_validate(ocr_data)
        migrated: dict[str, OcrResult] = {}
        for r in batch.results:
            k = resolve_key(r.filename)
            if k:
                migrated[k] = r
        shutil.copy(ocr_file, ocr_file.with_suffix(".json.bak"))
        save_ocr_results(output_path, migrated)

    if ext_old:
        ext_data = json.loads(ext_file.read_text(encoding="utf-8"))
        migrated = {}
        for fn, v in ext_data.items():
            k = resolve_key(fn)
            if k:
                migrated[k] = DocumentExtractionAdapter.validate_python(v)
        shutil.copy(ext_file, ext_file.with_suffix(".json.bak"))
        save_extractions(output_path, migrated)

    if dec_old:
        dec_data = json.loads(dec_file.read_text(encoding="utf-8"))
        migrated = {}
        for fn, v in dec_data.items():
            k = resolve_key(fn)
            if k:
                migrated[k] = ReviewDecision(**v)
        shutil.copy(dec_file, dec_file.with_suffix(".json.bak"))
        save_decisions(output_path, migrated)

    if name_old:
        name_data = json.loads(name_cache_file.read_text(encoding="utf-8"))
        migrated = {}
        for fn, v in name_data.items():
            k = resolve_key(fn)
            if k:
                migrated[k] = v
        shutil.copy(name_cache_file, name_cache_file.with_suffix(".json.bak"))
        save_name_cache(output_path, migrated)

    st.success("Migration complete. Backups saved as *.json.bak")
    st.rerun()
