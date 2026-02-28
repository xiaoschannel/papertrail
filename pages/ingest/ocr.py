import random
import traceback
from collections.abc import Callable
from pathlib import Path

import streamlit as st

from data import load_ocr_results
from models import OcrBatch, OcrResult
from ocr_providers import OCR_PROVIDERS, teardown_ocr
from settings import IMAGE_EXTENSIONS, get_config
from streamlit_progress import ProgressBar

st.title("OCR")


def get_image_files(path: Path) -> list[Path]:
    return [f for f in path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]


def get_already_processed(path: Path) -> set[str]:
    return {r.filename for r in load_ocr_results(path).results}


def run_batch(
    files: list[Path],
    results_file: Path,
    existing_results: list[OcrResult],
    ocr_fn: Callable[[Path], str],
    bar: ProgressBar,
) -> list[OcrResult]:
    new_results: list[OcrResult] = []
    for img_file in files:
        try:
            raw = ocr_fn(img_file)
            new_results.append(OcrResult(filename=img_file.name, raw=raw, boxes=None, markdown=raw, succeeded=True))
            bar.tick(True)
        except Exception:
            raw = traceback.format_exc()
            new_results.append(OcrResult(filename=img_file.name, raw=raw, boxes=None, markdown=raw, succeeded=False))
            bar.tick(False)
        results_file.write_text(
            OcrBatch(results=existing_results + new_results).model_dump_json(indent=2),
            encoding="utf-8",
        )
    return new_results


cfg = get_config()
input_dir = cfg.get("input_image_path", "")
output_dir = cfg.get("batch_output_path", "")
ocr_provider = st.selectbox("OCR Model", list(OCR_PROVIDERS.keys()))
mode = st.radio("Mode", ["Process new only", "Reprocess all", "Run failed"], horizontal=True)

input_path = Path(input_dir)
output_path = Path(output_dir)
all_images = get_image_files(input_path) if input_dir and output_dir else []
loaded = load_ocr_results(output_path).results if output_dir else []
results_file = output_path / "ocr.json"

if mode == "Reprocess all":
    to_process = all_images
    existing_results = []
elif mode == "Run failed":
    failed_names = {r.filename for r in loaded if not r.succeeded}
    to_process = [f for f in all_images if f.name in failed_names]
    existing_results = [r for r in loaded if r.succeeded]
else:
    already_done = get_already_processed(output_path) if output_dir else set()
    to_process = [f for f in all_images if f.name not in already_done]
    existing_results = loaded

n_total = len(all_images)
n_processed = sum(1 for r in loaded if r.succeeded)
n_failed = sum(1 for r in loaded if not r.succeeded)
n_new = len(to_process)

col0, col1, col2, col3 = st.columns(4)
col0.metric("Total", n_total)
col1.metric("Processed", n_processed)
col2.metric("New", n_new)
col3.metric("Failed", n_failed)

batch_limit = st.number_input("Batch size (0 = all)", min_value=0, value=0, step=1)
if batch_limit > 0:
    to_process = to_process[:batch_limit]

if not st.button("Start Batch Processing"):
    st.stop()

output_path.mkdir(parents=True, exist_ok=True)

if not to_process:
    st.info("No images to process.")
    st.stop()

random.shuffle(to_process)
st.info(f"Processing {len(to_process)} images...")

new_results = run_batch(
    files=to_process,
    results_file=results_file,
    existing_results=existing_results,
    ocr_fn=OCR_PROVIDERS[ocr_provider].run,
    bar=ProgressBar(len(to_process)),
)
teardown_ocr(ocr_provider)

n_fails_total = sum(1 for r in new_results if not r.succeeded)
st.success(
    f"Done! Processed {len(new_results)} new images "
    f"({len(existing_results) + len(new_results)} total, {n_fails_total} failed). Saved to {results_file}"
)
