import random
import traceback
from pathlib import Path

import streamlit as st

from data import load_ocr_results, save_ocr_results
from models import OcrResult, batch_serial_key, iter_indexed_files, load_scan_index
from ocr_providers import OCR_PROVIDERS, teardown_ocr
from settings import get_config
from streamlit_progress import ProgressBar

st.title("OCR")

cfg = get_config()
input_dir = cfg.get("input_image_path", "")
output_dir = cfg.get("batch_output_path", "")

if not input_dir or not output_dir:
    st.info("Set input image path and batch output path in Config first.")
    st.stop()

input_path = Path(input_dir)
output_path = Path(output_dir)
index_file = output_path / "batches.json"
if not index_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
indexed_items = iter_indexed_files(scan_index, include_archived=False)
loaded = load_ocr_results(output_path)
results_file = output_path / "ocr.json"

ocr_provider = st.selectbox("OCR Model", list(OCR_PROVIDERS.keys()))
mode = st.radio("Mode", ["Process new only", "Reprocess all", "Run failed"], horizontal=True)

if mode == "Reprocess all":
    to_process = [(batch_serial_key(bid, ser), input_path / fn) for bid, ser, fn in indexed_items if (input_path / fn).exists()]
    existing = {}
elif mode == "Run failed":
    failed_keys = {k for k, r in loaded.items() if not r.succeeded}
    to_process = [(k, input_path / fn) for bid, ser, fn in indexed_items if (k := batch_serial_key(bid, ser)) in failed_keys and (input_path / fn).exists()]
    existing = {k: r for k, r in loaded.items() if r.succeeded}
else:
    existing = dict(loaded)
    to_process = [(k, input_path / fn) for bid, ser, fn in indexed_items if (k := batch_serial_key(bid, ser)) not in existing and (input_path / fn).exists()]

n_total = len(indexed_items)
n_processed = sum(1 for r in loaded.values() if r.succeeded)
n_failed = sum(1 for r in loaded.values() if not r.succeeded)
n_new = len(to_process)

col0, col1, col2, col3 = st.columns(4)
col0.metric("Total", n_total)
col1.metric("Processed", n_processed)
col2.metric("New", n_new)
col3.metric("Failed", n_failed)

batch_limit = st.number_input("Batch size (0 = all)", min_value=0, value=0, step=1)
if batch_limit > 0:
    to_process = to_process[:batch_limit]

ocr_proceed = st.session_state.pop("ocr_reprocess_confirmed", False)

if mode == "Reprocess all" and st.button("Start Batch Processing") and not ocr_proceed:
    @st.dialog("Confirm Reprocess All")
    def confirm_ocr_reprocess():
        st.warning("This will replace all existing OCR results. This cannot be undone.")
        if st.button("Confirm", type="primary"):
            st.session_state["ocr_reprocess_confirmed"] = True
            st.rerun()

    confirm_ocr_reprocess()
    st.stop()

if not ocr_proceed and not st.button("Start Batch Processing"):
    st.stop()

output_path.mkdir(parents=True, exist_ok=True)

if not to_process:
    st.info("No images to process.")
    st.stop()

random.shuffle(to_process)
st.info(f"Processing {len(to_process)} images...")

new_results: dict[str, OcrResult] = {}
bar = ProgressBar(len(to_process))
for key, img_path in to_process:
    try:
        raw = OCR_PROVIDERS[ocr_provider].run(img_path)
        new_results[key] = OcrResult(filename=img_path.name, raw=raw, boxes=None, markdown=raw, succeeded=True)
        bar.tick(True)
    except Exception:
        raw = traceback.format_exc()
        new_results[key] = OcrResult(filename=img_path.name, raw=raw, boxes=None, markdown=raw, succeeded=False)
        bar.tick(False)
    merged = existing | new_results
    save_ocr_results(output_path, merged)

teardown_ocr(ocr_provider)

n_fails_total = sum(1 for r in new_results.values() if not r.succeeded)
st.success(
    f"Done! Processed {len(new_results)} new images "
    f"({len(existing) + len(new_results)} total, {n_fails_total} failed). Saved to {results_file}"
)
