import streamlit as st

from brand_registry import BrandDirectory, BrandEntry, load_brand_directory, save_brand_directory
from viz_data import clear_viz_data_cache

st.title("Brand registry")

directory = load_brand_directory()

st.markdown(
    "Each **brand** has an id, a display label, and **prefixes**. A receipt’s merchant name is matched by "
    "**longest prefix** (Latin is case-insensitive). Remainder after the prefix is **Location** in visualization."
)

for i, brand in enumerate(directory.brands):
    with st.expander(f"{brand.label} (`{brand.id}`)"):
        nl = st.text_input("Label", value=brand.label, key=f"br_lbl_{i}")
        ta = st.text_area("Prefixes (one per line)", value="\n".join(brand.prefixes), height=120, key=f"br_pref_{i}")
        c1, c2 = st.columns(2)
        if c1.button("Save", key=f"br_save_{i}"):
            lines = [p.strip() for p in ta.splitlines() if p.strip()]
            new_brands = list(directory.brands)
            new_brands[i] = BrandEntry(id=brand.id, label=nl.strip(), prefixes=lines)
            save_brand_directory(BrandDirectory(brands=new_brands))
            clear_viz_data_cache()
            st.rerun()
        if c2.button("Delete", key=f"br_del_{i}"):
            new_brands = [b for j, b in enumerate(directory.brands) if j != i]
            save_brand_directory(BrandDirectory(brands=new_brands))
            clear_viz_data_cache()
            st.rerun()

st.subheader("Add brand")
with st.form("add_brand"):
    nid = st.text_input("Id (slug)")
    nlab = st.text_input("Label")
    npref = st.text_area("Prefixes (one per line)", height=100)
    submitted = st.form_submit_button("Add")
    if submitted:
        lines = [p.strip() for p in npref.splitlines() if p.strip()]
        if nid.strip() and nlab.strip() and lines:
            if any(b.id == nid.strip() for b in directory.brands):
                st.error("Id already exists.")
            else:
                directory.brands.append(BrandEntry(id=nid.strip(), label=nlab.strip(), prefixes=lines))
                save_brand_directory(directory)
                clear_viz_data_cache()
                st.rerun()
