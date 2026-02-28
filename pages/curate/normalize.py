from pathlib import Path

import streamlit as st

from data import (
    load_distinct_pairs,
    load_name_normalizations,
    load_reorganized_state,
    read_sidecar,
    save_distinct_pairs,
    save_name_normalizations,
    write_sidecar,
)
from normalize_engines import ENGINES
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
distinct_pairs = load_distinct_pairs(output_path)

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

engine_ids = list(ENGINES.keys())
engine_labels = [ENGINES[eid].label for eid in engine_ids]
selected_label = st.radio("Engine", engine_labels, horizontal=True)
selected_engine_id = engine_ids[engine_labels.index(selected_label)]
engine = ENGINES[selected_engine_id]

eps = engine.render_slider(st, f"threshold_{selected_engine_id}")
with st.spinner("Running..."):
    cluster_map = engine.run(output_path, all_names, eps)


def filter_cluster_by_distinct_pairs(
    members: list[str], pairs: set[frozenset[str]]
) -> list[str]:
    filtered = []
    for m in members:
        others = [o for o in members if o != m]
        if others and all(frozenset({m, o}) in pairs for o in others):
            continue
        filtered.append(m)
    return filtered


visible_clusters: dict[int, list[str]] = {}
for cid, members in cluster_map.items():
    if not any(m in decision_names for m in members):
        continue
    filtered = filter_cluster_by_distinct_pairs(members, distinct_pairs)
    if len(filtered) >= 2:
        visible_clusters[cid] = filtered

st.metric("Clusters", len(visible_clusters))

if not visible_clusters:
    st.success("No name clusters at current setting.")
else:
    for cid, members in sorted(visible_clusters.items()):
        with st.expander(f"Cluster {cid + 1} — {len(members)} names"):
            toggle_key = f"toggle_all_{cid}"

            def _on_toggle(cid=cid, members=members):
                val = st.session_state[f"toggle_all_{cid}"]
                for m in members:
                    st.session_state[f"sel_{cid}_{m}"] = val

            st.checkbox("Toggle all", value=True, key=toggle_key, on_change=_on_toggle)

            checked: list[str] = []
            for m in members:
                count = len(name_to_filenames.get(m, []))
                label = f"{m} ({count})" if count else f"{m} (canonical only)"
                if m in canonical_names:
                    label += " [canonical]"
                if st.checkbox(label, value=True, key=f"sel_{cid}_{m}"):
                    checked.append(m)

            unchecked = [m for m in members if m not in checked]

            if len(checked) < 2:
                if unchecked:
                    if st.button("Confirm different", key=f"distinct_btn_{cid}"):
                        for u in unchecked:
                            for c in checked:
                                distinct_pairs.add(frozenset({u, c}))
                        save_distinct_pairs(output_path, distinct_pairs)
                        st.rerun()
                continue

            radio_options = []
            for m in checked:
                radio_options.append(f"{m} (canonical)" if m in canonical_names else m)
            canonical_in_checked = [m for m in checked if m in canonical_names]
            default_idx = checked.index(canonical_in_checked[0]) if canonical_in_checked else 0

            selected_radio = st.radio(
                "Normalize to", radio_options, index=default_idx, key=f"target_{cid}"
            )
            target = checked[radio_options.index(selected_radio)]
            to_merge = [m for m in checked if m != target]

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Normalize", key=f"norm_btn_{cid}"):
                    for variant in to_merge:
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

            with col2:
                if unchecked and st.button("Confirm different", key=f"distinct_btn_{cid}"):
                    for u in unchecked:
                        distinct_pairs.add(frozenset({u, target}))
                    save_distinct_pairs(output_path, distinct_pairs)
                    st.rerun()

if distinct_pairs:
    st.divider()
    st.subheader(f"Confirmed different pairs ({len(distinct_pairs)})")
    sorted_pairs = sorted(sorted(p) for p in distinct_pairs)
    for pair in sorted_pairs:
        pair_fs = frozenset(pair)
        col1, col2 = st.columns([4, 1])
        with col1:
            st.text(f"{pair[0]}  ↔  {pair[1]}")
        with col2:
            if st.button("Remove", key=f"rm_pair_{pair[0]}___{pair[1]}"):
                distinct_pairs.discard(pair_fs)
                save_distinct_pairs(output_path, distinct_pairs)
                st.rerun()
