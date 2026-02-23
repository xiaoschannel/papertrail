import streamlit as st

from settings import get_config, save_config

st.title("Config")

cfg = get_config()

with st.form("config_form"):
    input_image_path = st.text_input("Input image path", value=cfg.get("input_image_path", ""))
    batch_output_path = st.text_input("Batch output path", value=cfg.get("batch_output_path", ""))
    if st.form_submit_button("Save"):
        save_config({"input_image_path": input_image_path, "batch_output_path": batch_output_path})
        st.rerun()
