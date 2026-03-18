from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from data import _iter_year_month_dirs
from models import Sidecar
from settings import get_config

st.title("Sidecar Check")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)

sidecar_paths: list[Path] = []
for month_dir in _iter_year_month_dirs(output_path):
    sidecar_paths.extend(month_dir.glob("*.json"))
marked_dir = output_path / "marked"
if marked_dir.exists():
    sidecar_paths.extend(marked_dir.glob("*.json"))

valid_count = 0
invalid: list[tuple[str, str]] = []

for sp in sidecar_paths:
    try:
        Sidecar.model_validate_json(sp.read_text(encoding="utf-8"))
        valid_count += 1
    except ValidationError as e:
        rel = sp.relative_to(output_path).as_posix()
        invalid.append((rel, str(e)))

col1, col2, col3 = st.columns(3)
col1.metric("Total", valid_count + len(invalid))
col2.metric("Valid", valid_count)
col3.metric("Invalid", len(invalid))

if not invalid:
    st.success("All sidecars are valid.")
else:
    st.error(f"{len(invalid)} invalid sidecar(s) found.")
    for rel_path, error in invalid:
        with st.expander(rel_path):
            st.code(error)
