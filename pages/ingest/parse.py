import random
import time
from pathlib import Path

import streamlit as st

from data import build_document_index, load_extractions, load_ocr_results, save_extractions
from extraction import EXTRACTORS
from models import batch_serial_key, iter_indexed_files, load_scan_index
from settings import get_config
from streamlit_progress import ProgressBar

st.title("Parse")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)
index_file = output_path / "batches.json"
if not index_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
indexed_keys = {batch_serial_key(bid, ser) for bid, ser, _ in iter_indexed_files(scan_index, include_archived=False)}
loaded = load_ocr_results(output_path)
ocr_by_key = {k: r.markdown for k, r in loaded.items() if r.succeeded and k in indexed_keys}

index = build_document_index(output_path, indexed_keys, ocr_keys=set(ocr_by_key))
doc_keys_with_ocr = index.doc_keys_with_ocr(ocr_by_key)
extractions = {k: v for k, v in load_extractions(output_path).items() if k in {str(dk) for dk in doc_keys_with_ocr}}

extractor_name = st.selectbox("Model", list(EXTRACTORS.keys()))

mode = st.radio("Mode", ["Process new only", "Reprocess all"], horizontal=True)
if mode == "Reprocess all":
    to_process = list(doc_keys_with_ocr)
    existing_extractions = {}
else:
    existing_extractions = dict(extractions)
    to_process = [dk for dk in doc_keys_with_ocr if str(dk) not in existing_extractions]

n_total = len(doc_keys_with_ocr)
n_processed = len(extractions)
n_new = len(to_process)

batch_limit = st.number_input("Batch size (0 = all)", min_value=0, value=0, step=1)
if batch_limit > 0:
    to_process = to_process[:batch_limit]

col0, col1, col2 = st.columns(3)
col0.metric("Total", n_total)
col1.metric("Processed", n_processed)
col2.metric("New", n_new)

run_clicked = st.button("Run Extraction", width="stretch", type="primary")

if run_clicked and to_process:
    if mode == "Reprocess all":
        extractions.clear()

    random.shuffle(to_process)
    bar = ProgressBar(len(to_process))
    failed: list[str] = []
    last_save = time.time()

    for doc_key in to_process:
        ocr_text = index.concat_ocr(doc_key, ocr_by_key)
        try:
            extractions[str(doc_key)] = EXTRACTORS[extractor_name](ocr_text)
            bar.tick(True)
        except Exception:
            failed.append(str(doc_key))
            bar.tick(False)
        if time.time() - last_save > 15:
            save_extractions(output_path, extractions)
            last_save = time.time()
    save_extractions(output_path, extractions)

    if failed:
        st.warning(f"{len(failed)} / {len(to_process)} extraction(s) failed: {', '.join(failed)}")
    st.rerun()

if run_clicked and not to_process:
    st.info("No items to process.")

if not doc_keys_with_ocr:
    st.info("No OCR results. Run OCR first.")
