from pathlib import Path

import streamlit as st

from data import (
    build_document_index,
    load_decisions,
    load_extractions,
    save_decisions,
    save_extractions,
)
from models import batch_serial_key, iter_indexed_files, load_scan_index

st.title("Fix Leftover Extractions")

st.markdown(
    "Detects single-page extractions whose pages were later linked into multi-page groups. "
    "These appear in Review but are obsolete—the multi-page doc has its own extraction."
)

from settings import get_config

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
index_file = output_path / "batches.json"
if not index_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
indexed_keys = {batch_serial_key(bid, ser) for bid, ser, _ in iter_indexed_files(scan_index, include_archived=False)}
index = build_document_index(output_path, indexed_keys)
extractions = load_extractions(output_path)
decisions = load_decisions(output_path)

obsolete_extractions: list[str] = []
obsolete_decisions: list[str] = []

for key in list(extractions):
    dk = index.key_to_doc_key(key)
    if dk and dk.is_multi_page and str(dk) != key:
        obsolete_extractions.append(key)
        if key in decisions:
            obsolete_decisions.append(key)

if not obsolete_extractions:
    st.success("No leftover single-page extractions found.")
    st.stop()

st.warning(f"Found {len(obsolete_extractions)} obsolete extraction(s) and {len(obsolete_decisions)} matching decision(s).")
st.markdown("These single-page keys are now part of multi-page groups:")
for k in sorted(obsolete_extractions):
    dk = index.key_to_doc_key(k)
    parent = str(dk) if dk else "?"
    st.code(f"{k} → superseded by {parent}")

if st.button("Remove Obsolete Extractions & Decisions", type="primary", width="stretch"):
    for k in obsolete_extractions:
        extractions.pop(k, None)
        decisions.pop(k, None)
    save_extractions(output_path, extractions)
    save_decisions(output_path, decisions)
    st.success(f"Removed {len(obsolete_extractions)} extraction(s) and {len(obsolete_decisions)} decision(s).")
    st.rerun()
