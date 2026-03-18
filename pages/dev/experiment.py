import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from PIL import Image, ImageEnhance

from box_drawing import draw_all_boxes, draw_field_boxes
from extraction import EXTRACTORS, build_extraction_prompt
from ocr_providers import OCR_PROVIDERS, run_ocr
from ocr_providers.deepseek import parse_grounding_output

st.title("Experiment")

uploaded = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg", "webp"])

if not uploaded:
    st.stop()

if st.session_state.get("exp_file") != uploaded.name:
    st.session_state.exp_file = uploaded.name
    for k in ["exp_plain", "exp_structured_raw", "exp_boxes", "exp_extraction"]:
        st.session_state.pop(k, None)

original = Image.open(uploaded).convert("RGB")

# ─── Preprocess ───────────────────────────────────────────────
st.header("Preprocess")

enhance = st.checkbox("Enhance image before OCR")

if enhance:
    col_orig, col_ctrl, col_preview = st.columns(3)
    with col_orig:
        st.image(original, caption="Original", width="stretch")
    with col_ctrl:
        denoise_first = st.checkbox("Denoise first", value=False)
        denoise_after = st.checkbox("Denoise after", value=False)
        if denoise_first or denoise_after:
            denoise_strength = st.slider("Denoise strength", 3, 15, 6, 1)
        method = st.radio("Method", ["CLAHE", "Contrast + Gamma"], horizontal=True)
        if method == "CLAHE":
            clip = st.slider("Clip limit", 1.0, 10.0, 3.0, 0.5)
            grid = st.slider("Grid size", 2, 16, 8, 1)
        else:
            contrast_val = st.slider("Contrast", 0.5, 3.0, 2.5, 0.1)
            gamma_val = st.slider("Gamma", 0.2, 3.0, 0.5, 0.1)

    work = np.array(original)
    if denoise_first:
        work = cv2.fastNlMeansDenoisingColored(work, None, denoise_strength, denoise_strength, 7, 21)
    work = Image.fromarray(work)
    if method == "CLAHE":
        lab = cv2.cvtColor(np.array(work), cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        image = Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
    else:
        image = ImageEnhance.Contrast(work).enhance(contrast_val)
        if gamma_val != 1.0:
            lut = [int(((i / 255.0) ** (1.0 / gamma_val)) * 255) for i in range(256)]
            image = image.point(lut * 3)
    if denoise_after:
        img_arr = cv2.fastNlMeansDenoisingColored(np.array(image), None, denoise_strength, denoise_strength, 7, 21)
        image = Image.fromarray(img_arr)

    with col_preview:
        st.image(image, caption="Enhanced", width="stretch")
else:
    st.image(original, caption="Uploaded", width=200)
    image = original

# ─── OCR ──────────────────────────────────────────────────────
st.header("OCR")

ocr_provider = st.selectbox("OCR Model", list(OCR_PROVIDERS.keys()))
is_deepseek = "DeepSeek" in ocr_provider

if st.button("Run OCR"):
    fd, tmp_str = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    tmp_path = Path(tmp_str)
    image.save(tmp_path)
    with st.spinner("Running plain OCR..."):
        st.session_state.exp_plain = run_ocr(tmp_path, provider=ocr_provider, structured=False)
    if is_deepseek:
        with st.spinner("Running structured OCR..."):
            raw_structured = run_ocr(tmp_path, provider=ocr_provider, structured=True)
        st.session_state.exp_structured_raw = raw_structured
        st.session_state.exp_boxes = parse_grounding_output(raw_structured)
    st.session_state.pop("exp_extraction", None)
    tmp_path.unlink()

has_plain = "exp_plain" in st.session_state
has_structured = "exp_structured_raw" in st.session_state
if not has_plain:
    st.stop()

if has_structured:
    col_boxes, col_structured, col_plain = st.columns(3)
    with col_boxes:
        st.markdown("**Structured — Boxes**")
        boxes = st.session_state.get("exp_boxes")
        if boxes:
            st.image(draw_all_boxes(image, boxes), caption="Detected boxes", width="stretch")
        else:
            st.image(image, caption="No boxes detected", width="stretch")
    with col_structured:
        st.markdown("**Structured**")
        st.text_area("Structured output", st.session_state.exp_structured_raw, height=600, label_visibility="collapsed")
    with col_plain:
        st.markdown("**Plain — Markdown**")
        display_p = st.radio("Display", ["Markdown", "Raw"], horizontal=True, key="disp_plain")
        if display_p == "Raw":
            st.text_area("Plain raw", st.session_state.exp_plain, height=600, label_visibility="collapsed")
        else:
            st.markdown(st.session_state.exp_plain.replace("\n", "  \n"), unsafe_allow_html=True)
else:
    col_img, col_text = st.columns(2)
    with col_img:
        st.image(image, caption="Image", width="stretch")
    with col_text:
        ocr_text = st.session_state.exp_plain
        display = st.radio("Display", ["Markdown", "Raw"], horizontal=True)
        if display == "Raw":
            st.text_area("OCR output", ocr_text, height=600, label_visibility="collapsed")
        else:
            st.markdown(ocr_text.replace("\n", "  \n"), unsafe_allow_html=True)

# ─── Parse ────────────────────────────────────────────────────
st.header("Parse")

plain_text = st.session_state.exp_plain
parse_boxes = st.session_state.get("exp_boxes")

ocr_text = f"--- Page 1 ---\n{plain_text}"
has_boxes = bool(parse_boxes)
if has_boxes:
    box_lines = [f"[P1-BOX-{idx}] {box.text}" for idx, box in enumerate(parse_boxes)]
    ocr_text += "\n--- Page 1 Grounding Boxes ---\n" + "\n".join(box_lines)

extractor_name = st.selectbox("Extractor", list(EXTRACTORS.keys()))

prompt = build_extraction_prompt(ocr_text, has_boxes=has_boxes)
with st.expander("Extraction prompt"):
    st.text(prompt)

if st.button("Run Parse"):
    with st.spinner("Extracting..."):
        st.session_state.exp_extraction = EXTRACTORS[extractor_name](ocr_text, has_boxes=has_boxes)

extraction = st.session_state.get("exp_extraction")
if not extraction:
    st.stop()

col_viz, col_json = st.columns(2)
with col_viz:
    st.markdown("**Visualization**")
    field_sources = getattr(extraction, "field_sources", {})
    if has_boxes and field_sources:
        st.image(draw_field_boxes(image, 1, parse_boxes, field_sources), width="stretch")
    else:
        st.image(image, width="stretch")
with col_json:
    st.markdown("**Extraction Result**")
    st.json(extraction.model_dump(), expanded=True)
