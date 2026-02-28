import random
import time
from pathlib import Path

import streamlit as st

from data import load_extractions, load_ocr_results, save_extractions
from extraction import EXTRACTORS
from settings import get_config
from streamlit_progress import ProgressBar

st.title("Parse")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)
batch = load_ocr_results(output_path)
succeeded = [r for r in batch.results if r.succeeded]
all_filenames = [r.filename for r in succeeded]
ocr_by_file = {r.filename: r.markdown for r in succeeded}

extractions = {k: v for k, v in load_extractions(output_path).items() if k in ocr_by_file}


extractor_name = st.selectbox("Model", list(EXTRACTORS.keys()))

mode = st.radio("Mode", ["Process new only", "Reprocess all"], horizontal=True)
if mode == "Reprocess all":
    to_process = all_filenames
    existing_extractions = {}
else:
    existing_extractions = dict(extractions)
    to_process = [f for f in all_filenames if f not in existing_extractions]

n_total = len(all_filenames)
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

    for filename in to_process:
        try:
            extractions[filename] = EXTRACTORS[extractor_name](ocr_by_file[filename])
            bar.tick(True)
        except Exception:
            failed.append(filename)
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

if not all_filenames:
    st.info("No OCR results. Run OCR first.")
