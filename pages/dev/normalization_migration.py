from pathlib import Path

import streamlit as st

from data import load_name_normalizations, save_name_normalizations
from settings import get_config


def flatten_normalizations(normalizations: dict[str, str]) -> dict[str, str]:
    def resolve_target(source: str) -> str:
        target = normalizations.get(source, source)
        seen = {source}
        while target in normalizations and target not in seen:
            seen.add(target)
            target = normalizations[target]
        return target

    flattened: dict[str, str] = {}
    for source in normalizations:
        target = resolve_target(source)
        if source != target:
            flattened[source] = target
    return flattened


st.title("Normalization Migration")

cfg = get_config()
batch_dir = (cfg.batch_output_path or "").strip()

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
normalizations = load_name_normalizations(output_path)
flattened = flatten_normalizations(normalizations)

before_values = set(normalizations.values())
after_values = set(flattened.values())
rewired_entries = sum(
    1
    for source, target in normalizations.items()
    if source in flattened and flattened[source] != target
)
removed_entries = len(normalizations) - len(flattened)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Entries Before", len(normalizations))
col2.metric("Entries After", len(flattened))
col3.metric("Rewired Entries", rewired_entries)
col4.metric("Removed Entries", removed_entries)

if normalizations == flattened:
    st.success("No migration needed. Normalizations are already flattened.")
else:
    st.info(
        f"Canonical targets reduced from {len(before_values)} to {len(after_values)}."
    )
    preview_rows = [
        {"source": source, "before": target, "after": flattened.get(source, "")}
        for source, target in sorted(normalizations.items())
        if flattened.get(source, "") != target
    ]
    st.dataframe(preview_rows, width="stretch", hide_index=True)

    if st.button("Run Flatten Migration", type="primary"):
        save_name_normalizations(output_path, flattened)
        st.success(
            f"Migration complete. Updated {rewired_entries} entries and removed {removed_entries} stale mappings."
        )
