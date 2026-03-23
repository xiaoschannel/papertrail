from pathlib import Path

import streamlit as st

from migration_location_address import collect_migration_preview, run_migration_apply
from settings import get_config

st.title("Location to address migration")

st.markdown(
    "Renames receipt `location` (street address) to `address` in `extractions.json` and archive sidecar JSON. "
    "Run **Preview** first, then **Apply** after upgrading the app code."
)

cfg = get_config()
default_path = cfg.batch_output_path or ""
path_str = st.text_input("Batch output path", value=default_path, key="mig_batch_path")
input_path = Path(cfg.input_image_path) if (cfg.input_image_path or "").strip() else None
if input_path:
    st.caption(f"Index comparison uses input image path: `{input_path}` (same as Index Audit).")

col_a, col_b = st.columns(2)
with col_a:
    preview = st.button("Preview (dry run)", width="stretch")
with col_b:
    apply = st.button("Apply migration", type="primary", width="stretch")

if preview:
    if not path_str.strip():
        st.error("Set a batch output path.")
    else:
        out = Path(path_str.strip())
        if not out.is_dir():
            st.error("Path is not a directory.")
        else:
            stats = collect_migration_preview(out, input_path)
            st.subheader("Preview")
            if stats.extractions_path:
                st.write(f"`extractions.json`: {stats.extractions_receipts_updated} receipt(s) would change.")
            else:
                st.write("No `extractions.json` found.")
            c1, c2, c3 = st.columns(3)
            c1.metric("Sidecar JSON total (under YYYY/MM)", f"{stats.total_sidecar_json:,}")
            c2.metric("Would change", f"{len(stats.sidecar_paths):,}")
            c3.metric("No change needed", f"{stats.sidecars_no_change_needed:,}")
            st.caption(
                "Sidecar total = would change + no change needed (same tree as migration: "
                "`batch_output/<year>/<month|undated>/*.json`)."
            )
            st.subheader("Output vs Index Audit")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("marked/ (non-.json files)", f"{stats.marked_non_json:,}")
            r2.metric("tossed/ (non-.json files)", f"{stats.tossed_non_json:,}")
            r3.metric("Sum (sidecar JSON + marked + tossed)", f"{stats.output_total_for_compare:,}")
            if stats.index_audit_not_on_input is not None:
                r4.metric("Index Audit: indexed not on input", f"{stats.index_audit_not_on_input:,}")
            else:
                r4.metric("Index Audit: indexed not on input", "—")
            st.caption(
                "marked/ and tossed/ use the same rule as organized scans: files that are not `.json`. "
                "Sum counts one sidecar JSON per archived doc plus one file per marked/tossed item. "
                "Index Audit column matches **Index Audit** when `batches.json` and input path are set."
            )
            if stats.index_audit_not_on_input is not None:
                delta = stats.output_total_for_compare - stats.index_audit_not_on_input
                st.write(
                    f"**Sum − Index Audit** = {delta:+,} "
                    f"(positive means more on-disk artifacts than that index metric; "
                    f"duplicates in `batches.json`, deleted outputs, or extra non-index files can explain gaps.)"
                )
            if stats.sidecar_paths:
                for rel in stats.sidecar_paths[:50]:
                    st.caption(rel)
                if len(stats.sidecar_paths) > 50:
                    st.caption(f"… and {len(stats.sidecar_paths) - 50} more")

if apply:
    if not path_str.strip():
        st.error("Set a batch output path.")
    else:
        out = Path(path_str.strip())
        if not out.is_dir():
            st.error("Path is not a directory.")
        else:
            stats = run_migration_apply(out, input_path)
            st.success("Migration finished.")
            st.write(f"`extractions.json` receipts updated: **{stats.extractions_receipts_updated}**")
            c1, c2, c3 = st.columns(3)
            c1.metric("Sidecar JSON total (under YYYY/MM)", f"{stats.total_sidecar_json:,}")
            c2.metric("Updated", f"{stats.sidecars_updated:,}")
            c3.metric("Unchanged", f"{stats.sidecars_no_change_needed:,}")
            st.caption("Sidecar total = updated + unchanged.")
            st.subheader("Output vs Index Audit")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("marked/ (non-.json files)", f"{stats.marked_non_json:,}")
            r2.metric("tossed/ (non-.json files)", f"{stats.tossed_non_json:,}")
            r3.metric("Sum (sidecar JSON + marked + tossed)", f"{stats.output_total_for_compare:,}")
            if stats.index_audit_not_on_input is not None:
                r4.metric("Index Audit: indexed not on input", f"{stats.index_audit_not_on_input:,}")
            else:
                r4.metric("Index Audit: indexed not on input", "—")
            if stats.index_audit_not_on_input is not None:
                delta = stats.output_total_for_compare - stats.index_audit_not_on_input
                st.write(f"**Sum − Index Audit** = {delta:+,}")
