from pathlib import Path

import streamlit as st
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_distances

from data import (
    load_name_normalizations,
    load_reorganized_state,
    read_sidecar,
    save_name_normalizations,
    write_sidecar,
)
from name_similarity import DEFAULT_THRESHOLD, ensure_embeddings
from organize_utils import apply_reorganize
from settings import get_config

st.title("Normalize")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)
_, accepted_metadata = load_reorganized_state(output_path)
if not accepted_metadata:
    st.info("No organized files found. Run Archive first.")
    st.stop()

normalizations = load_name_normalizations(output_path)

decision_names: set[str] = set()
name_to_filenames: dict[str, list[str]] = {}
for fn, entry in accepted_metadata.items():
    name = entry.get("review", {}).get("name", "")
    if name:
        decision_names.add(name)
        name_to_filenames.setdefault(name, []).append(fn)

canonical_names: set[str] = set(normalizations.values())

all_names_set = decision_names | canonical_names
all_names = sorted(all_names_set)

if not all_names:
    st.info("No names to normalize.")
    st.stop()

with st.spinner("Updating embeddings..."):
    cached_names, cached_matrix = ensure_embeddings(output_path, all_names)
cached_lookup: dict[str, int] = {n: i for i, n in enumerate(cached_names)}

indices = [cached_lookup[n] for n in all_names]
embedding_matrix = cached_matrix[indices]

step = DEFAULT_THRESHOLD / 20
threshold = st.slider("Distance threshold",min_value=step, max_value=step*100, value=DEFAULT_THRESHOLD, step=step)

dist_matrix = cosine_distances(embedding_matrix)
clustering = DBSCAN(eps=threshold, min_samples=2, metric="precomputed").fit(dist_matrix)

labels = clustering.labels_
cluster_ids = set(labels)
cluster_ids.discard(-1)

cluster_map: dict[int, list[str]] = {}
for i, label in enumerate(labels):
    if label == -1:
        continue
    cluster_map.setdefault(label, []).append(all_names[i])

visible_clusters = {
    cid: members
    for cid, members in cluster_map.items()
    if any(m in decision_names for m in members)
}

st.metric("Clusters", len(visible_clusters))

if not visible_clusters:
    st.success("No name clusters at current threshold.")
    st.stop()

for cid, members in sorted(visible_clusters.items()):
    cluster_canonical = [m for m in members if m in canonical_names]
    has_canonical = len(cluster_canonical) > 0

    header_parts = []
    for m in members:
        count = len(name_to_filenames.get(m, []))
        suffix = f" ({count})" if count else " (canonical only)"
        tag = " **[canonical]**" if m in canonical_names else ""
        header_parts.append(f"- {m}{suffix}{tag}")

    with st.expander(f"Cluster {cid + 1} — {len(members)} names"):
        st.markdown("\n".join(header_parts))

        if has_canonical:
            locked_name = cluster_canonical[0]
            st.info(f"Canonical name: **{locked_name}**")
            target = locked_name
        else:
            target = st.selectbox(
                "Normalize to",
                members,
                key=f"norm_target_{cid}",
            )

        variants = [m for m in members if m != target]
        if variants and st.button("Normalize", key=f"norm_btn_{cid}"):
            for variant in variants:
                normalizations[variant] = target
                for fn in name_to_filenames.get(variant, []):
                    path_str = accepted_metadata.get(fn, {}).get("_path", "")
                    if not path_str:
                        continue
                    entry = read_sidecar(output_path / path_str)
                    if entry:
                        entry["review"]["name"] = target
                        write_sidecar(output_path / path_str, entry)
            save_name_normalizations(output_path, normalizations)
            moves = apply_reorganize(output_path)
            if moves:
                st.toast(f"Reorganized {len(moves)} file(s)")
            st.rerun()
