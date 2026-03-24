import streamlit as st
import pandas as pd

from brand_registry import (
    BrandDirectory,
    BrandEntry,
    build_prefix_suggestions,
    load_brand_directory,
    make_brand_id,
    save_brand_directory,
)
from settings import get_config
from viz_data import clear_viz_data_cache, get_output_path, load_viz_records

st.title("Brand registry")

directory = load_brand_directory()
cfg = get_config()

st.markdown(
    "Each **brand** has a display label and **prefixes**. A receipt’s merchant name is matched by "
    "**longest prefix** (Latin is case-insensitive). Remainder after the prefix is **Location** in visualization."
)

output_path = get_output_path()
records_df = pd.DataFrame()
if output_path:
    records_df = load_viz_records(str(output_path))


def create_brand(label: str, prefix: str) -> None:
    normalized_label = label.strip()
    normalized_prefix = prefix.strip()
    if not normalized_label or not normalized_prefix:
        return
    existing_ids = {brand.id for brand in directory.brands}
    new_id = make_brand_id(normalized_label, existing_ids)
    new_brands = [*directory.brands, BrandEntry(id=new_id, label=normalized_label, prefixes=[normalized_prefix])]
    save_brand_directory(BrandDirectory(brands=new_brands))
    clear_viz_data_cache()
    st.rerun()


receipts_df = records_df[records_df["document_type"] == "receipt"].copy() if not records_df.empty else pd.DataFrame()
if not receipts_df.empty and "brand_id" in receipts_df.columns:
    unmatched_mask = receipts_df["brand_id"].isna() | (receipts_df["brand_id"] == "")
    unmatched_df = receipts_df[unmatched_mask].copy()
else:
    unmatched_df = pd.DataFrame()

SECTION_OPTIONS = ("Overview", "Suggestions", "Manage brands")
st.radio(
    "Section",
    SECTION_OPTIONS,
    horizontal=True,
    key="brand_registry_section",
    label_visibility="collapsed",
)
section = st.session_state.brand_registry_section

if section == "Overview":
    if receipts_df.empty:
        st.info("No receipt data available yet. Configure output path and archive receipts first.")
    else:
        total_receipts = int(len(receipts_df))
        unmatched_count = int(len(unmatched_df))
        matched_count = total_receipts - unmatched_count
        c1, c2, c3 = st.columns(3)
        c1.metric("Total receipts", total_receipts)
        c2.metric("Matched receipts", matched_count)
        c3.metric("Unmatched receipts", unmatched_count)
        unmatched_name_counts = unmatched_df["name"].fillna("").astype(str).str.strip()
        unmatched_name_counts = unmatched_name_counts[unmatched_name_counts != ""].value_counts()
        if not unmatched_name_counts.empty:
            unmatched_sort = st.radio("Sort unmatched names by", options=["Frequency", "Alphabetical"], horizontal=True)
            if unmatched_sort == "Alphabetical":
                unmatched_rows = [{"name": name, "count": int(count)} for name, count in sorted(unmatched_name_counts.items(), key=lambda x: x[0].casefold())]
            else:
                unmatched_rows = [{"name": name, "count": int(count)} for name, count in unmatched_name_counts.items()]
            st.dataframe(pd.DataFrame(unmatched_rows), width="stretch", hide_index=True)

elif section == "Suggestions":
    with st.form("prefix_suggestion_controls"):
        boundary_only = st.checkbox("Boundary-only mode", value=cfg.prefix_suggestion_boundary_only)
        c1, c2, c3 = st.columns(3)
        with c1:
            max_length = st.number_input("Max prefix length", min_value=4, max_value=80, value=cfg.prefix_suggestion_max_length, step=1)
        with c2:
            min_length = st.number_input("Min prefix length", min_value=1, max_value=24, value=cfg.prefix_suggestion_min_length, step=1)
        with c3:
            min_count = st.number_input("Min count", min_value=1, max_value=50, value=cfg.prefix_suggestion_min_count, step=1)
        st.form_submit_button("Refresh suggestions")

    unmatched_names = unmatched_df["name"].fillna("").astype(str).tolist() if not unmatched_df.empty else []
    suggestions = build_prefix_suggestions(
        unmatched_names,
        boundary_only=bool(boundary_only),
        max_length=int(max_length),
        min_length=int(min_length),
        min_count=int(min_count),
    )
    if suggestions:
        target_brand_ids = [b.id for b in directory.brands]
        brand_label_by_id = {b.id: b.label for b in directory.brands}
        target_brand = (
            st.selectbox(
                "Target brand for quick prefix add",
                options=target_brand_ids,
                index=0,
                format_func=lambda brand_id: brand_label_by_id.get(brand_id, brand_id),
            )
            if target_brand_ids
            else ""
        )
        suggestion_rows = [{"prefix": s.prefix, "count": s.count} for s in suggestions[:120]]
        st.dataframe(pd.DataFrame(suggestion_rows), width="stretch", hide_index=True)
        for idx, suggestion in enumerate(suggestions[:30]):
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.write(f"`{suggestion.prefix}` ({suggestion.count})")
            if c2.button("Create brand", key=f"sg_create_{idx}"):
                create_brand(suggestion.prefix.title(), suggestion.prefix)
            if target_brand and c3.button("Add prefix", key=f"sg_add_{idx}"):
                new_brands = []
                for brand in directory.brands:
                    if brand.id == target_brand:
                        merged = list(dict.fromkeys([*brand.prefixes, suggestion.prefix]))
                        new_brands.append(BrandEntry(id=brand.id, label=brand.label, prefixes=merged))
                    else:
                        new_brands.append(brand)
                save_brand_directory(BrandDirectory(brands=new_brands))
                clear_viz_data_cache()
                st.rerun()

else:
    search_text = st.text_input("Search brands")
    filtered_brands = directory.brands
    if search_text.strip():
        q = search_text.casefold().strip()
        filtered_brands = [b for b in directory.brands if q in b.id.casefold() or q in b.label.casefold() or any(q in p.casefold() for p in b.prefixes)]

    for i, brand in enumerate(filtered_brands):
        with st.expander(brand.label):
            nl = st.text_input("Label", value=brand.label, key=f"br_lbl_{brand.id}_{i}")
            ta = st.text_area("Prefixes (one per line)", value="\n".join(brand.prefixes), height=120, key=f"br_pref_{brand.id}_{i}")
            c1, c2 = st.columns(2)
            if c1.button("Save", key=f"br_save_{brand.id}_{i}"):
                lines = [p.strip() for p in ta.splitlines() if p.strip()]
                new_brands = []
                for existing in directory.brands:
                    if existing.id == brand.id:
                        new_brands.append(BrandEntry(id=existing.id, label=nl.strip(), prefixes=lines))
                    else:
                        new_brands.append(existing)
                save_brand_directory(BrandDirectory(brands=new_brands))
                clear_viz_data_cache()
                st.rerun()
            if c2.button("Delete", key=f"br_del_{brand.id}_{i}"):
                new_brands = [b for b in directory.brands if b.id != brand.id]
                save_brand_directory(BrandDirectory(brands=new_brands))
                clear_viz_data_cache()
                st.rerun()

    with st.form("add_brand"):
        nlab = st.text_input("Label", key="add_brand_label")
        npref = st.text_area("Prefixes (one per line)", height=100, key="add_brand_prefixes")
        submitted = st.form_submit_button("Add")
        if submitted:
            lines = [p.strip() for p in npref.splitlines() if p.strip()]
            if nlab.strip() and lines:
                existing_ids = {brand.id for brand in directory.brands}
                new_id = make_brand_id(nlab.strip(), existing_ids)
                new_brands = [*directory.brands, BrandEntry(id=new_id, label=nlab.strip(), prefixes=lines)]
                save_brand_directory(BrandDirectory(brands=new_brands))
                clear_viz_data_cache()
                st.session_state["add_brand_label"] = ""
                st.session_state["add_brand_prefixes"] = ""
                st.rerun()
