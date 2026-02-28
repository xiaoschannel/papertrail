from pathlib import Path

import streamlit as st

from data import scan_organized_filenames
from models import load_scan_index
from settings import get_config

st.title("Sanity Check")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
batches_path = output_path / "batches.json"
if not batches_path.exists():
    st.info("No batches.json found. Run File Index first.")
    st.stop()

organized = scan_organized_filenames(output_path)
scan_index, _ = load_scan_index(output_path)

st.header("Batch Sanity")
for batch in scan_index.batches:
    files_in_batch = set(batch.files.values())
    files_organized = files_in_batch & organized
    files_missing = files_in_batch - organized

    st.subheader(f"Batch {batch.batch_id}")
    col0, col1, col2 = st.columns(3)
    col0.metric("Files in batch", len(files_in_batch))
    col1.metric("Files organized", len(files_organized))
    if files_missing:
        col2.error(f"Files missing: {', '.join(sorted(files_missing))}")
    else:
        col2.success("Clear!")

st.header("Archive Sanity")
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

failures: list[tuple[str, str, str]] = []

for folder in sorted(set(accepted_by_folder) | set(sidecar_stems_by_folder)):
    data_stems = {Path(f).stem for f in accepted_by_folder.get(folder, set())}
    sidecars = sidecar_stems_by_folder.get(folder, set())
    missing_sidecar = sorted(data_stems - sidecars)
    extra_sidecar = sorted(sidecars - data_stems)
    if missing_sidecar or extra_sidecar:
        detail = f"missing_sidecar={missing_sidecar}, extra_sidecar={extra_sidecar}"
        failures.append((folder, "data files vs sidecar mismatch", detail))

if not failures:
    st.success("Sanity checks passed for batch coverage and archive metadata.")
else:
    st.error(f"{len(failures)} issue(s) found.")
    by_item: dict[str, list[tuple[str, str]]] = {}
    for item, issue, detail in failures:
        by_item.setdefault(item, []).append((issue, detail))
    for item, issues in sorted(by_item.items()):
        with st.expander(f"{item} — {len(issues)} issue(s)"):
            for issue, detail in issues:
                msg = f"**{issue}**"
                if detail:
                    msg += f" — {detail}"
                st.markdown(msg)
