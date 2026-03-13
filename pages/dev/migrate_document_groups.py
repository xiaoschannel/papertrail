from pathlib import Path

import streamlit as st

from data import load_document_groups, save_document_groups
from settings import get_config

st.title("Migrate Document Groups")

st.markdown(
    "Strip single-page groups from documents.json. Single-page documents are inferred by DocumentIndex; "
    "storing them explicitly is redundant."
)

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
doc_file = output_path / "documents.json"

if not doc_file.exists():
    st.success("No documents.json to migrate.")
    st.stop()

doc = load_document_groups(output_path)
before = len(doc.groups)
doc.groups = [g for g in doc.groups if len(g) > 1]
after = len(doc.groups)
removed = before - after

if removed == 0:
    st.success("No single-page groups to remove.")
    st.stop()

st.info(f"Would remove {removed} single-page group(s). {after} multi-page group(s) remain.")

if st.button("Run Migration", width="stretch", type="primary"):
    save_document_groups(output_path, doc)
    st.success(f"Removed {removed} single-page group(s).")
    st.rerun()
