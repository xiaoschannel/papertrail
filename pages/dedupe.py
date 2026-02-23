import shutil
from pathlib import Path

import streamlit as st

from data import load_reorganized_state, sidecar_path_for
from dedupe_candidates import find_dedupe_clusters
from models import ReviewDecision
from settings import get_config

st.title("Dedupe")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)
tossed_fns, accepted_metadata = load_reorganized_state(output_path)
if not accepted_metadata and not tossed_fns:
    st.info("No organized files found. Run Archive first.")
    st.stop()

records: dict[str, ReviewDecision] = {}
for fn, entry in accepted_metadata.items():
    if entry.get("review"):
        records[fn] = ReviewDecision(**entry["review"])

clusters = find_dedupe_clusters(records)

st.metric("Clusters found", len(clusters))

if not clusters:
    st.success("No potential duplicates found.")
    st.stop()

for idx, cluster in enumerate(clusters):
    decs = [(fn, records[fn]) for fn in cluster]
    first = decs[0][1]
    st.subheader(f"Cluster {idx + 1} — {first.date} ~{first.time}")
    cols = st.columns(6)
    for i, (fn, dec) in enumerate(decs):
        col = cols[i % 6]
        is_tossed = fn in tossed_fns
        with col:
            label = f"~~{fn}~~\n\n~~{dec.name} — {dec.cost} {dec.currency}~~" if is_tossed else f"**{fn}**\n\n{dec.name} — {dec.cost} {dec.currency}"
            st.markdown(label)
            if not is_tossed and st.button("Toss", key=f"toss_{fn}", width="stretch"):
                path_str = accepted_metadata.get(fn, {}).get("_path", "")
                if path_str:
                    src = output_path / path_str
                    dst = output_path / "tossed" / fn
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.exists():
                        shutil.move(str(src), str(dst))
                    src_sidecar = sidecar_path_for(src)
                    if src_sidecar.exists():
                        shutil.move(str(src_sidecar), str(sidecar_path_for(dst)))
                st.rerun()
            if not is_tossed:
                path_str = accepted_metadata.get(fn, {}).get("_path", "")
                if path_str:
                    src = output_path / path_str
                    if src.exists():
                        st.image(str(src), width="stretch")
