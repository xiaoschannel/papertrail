import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from PIL import Image, ImageEnhance

from ocr_providers import OCR_PROVIDERS, run_ocr

st.title("OCR Experiment")

ocr_provider = st.selectbox("OCR Model", list(OCR_PROVIDERS.keys()))
uploaded = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg", "webp"])

if not uploaded:
    st.stop()

if st.session_state.get("explorer_file") != uploaded.name:
    st.session_state.explorer_file = uploaded.name
    st.session_state.pop("explorer_ocr", None)

original = Image.open(uploaded).convert("RGB")

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

if st.button("Run OCR"):
    fd, tmp_str = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    tmp_path = Path(tmp_str)
    image.save(tmp_path)
    with st.spinner("Running OCR..."):
        st.session_state.explorer_ocr = run_ocr(tmp_path, provider=ocr_provider)
    tmp_path.unlink()

if "explorer_ocr" not in st.session_state:
    st.stop()

st.divider()
col_img, col_text = st.columns(2)

with col_img:
    st.image(image, caption="Image", width="stretch")

with col_text:
    ocr_text = st.session_state.explorer_ocr
    display = st.radio("Display", ["Markdown", "Raw"], horizontal=True)
    if display == "Raw":
        st.text_area("OCR output", ocr_text, height=600, label_visibility="collapsed")
    else:
        st.markdown(ocr_text, unsafe_allow_html=True)
