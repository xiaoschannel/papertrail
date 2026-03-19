from pathlib import Path

import streamlit as st

from data import (
    load_name_cache,
    load_smart_match_cache,
    save_smart_match_cache,
)
from settings import get_config

st.title("Migrate Smart Match Cache")

cfg = get_config()
batch_dir = cfg.batch_output_path
if not batch_dir:
    st.stop()

output_path = Path(batch_dir)
legacy_path = output_path / "name_cache.json"

if not legacy_path.exists():
    st.info("No name_cache.json found. Nothing to migrate.")
    st.stop()

legacy = load_name_cache(output_path)
current = load_smart_match_cache(output_path)
st.write(f"Legacy rows: **{len(legacy)}** · Existing smart_match_cache rows: **{len(current)}**")

if st.button("Migrate", type="primary", width="stretch"):
    smart = dict(current)
    added = 0
    for doc_key, entry in legacy.items():
        if doc_key in smart:
            continue
        smart[doc_key] = {
            "extracted": entry.get("extracted", ""),
            "confirmed": entry.get("confirmed", ""),
            "extracted_phone": entry.get("extracted_phone", ""),
        }
        added += 1
    save_smart_match_cache(output_path, smart)
    legacy_path.unlink()
    st.success(f"Migrated **{added}** new row(s) into smart_match_cache.json. Removed name_cache.json.")
