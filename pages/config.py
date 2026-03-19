import streamlit as st

from extraction import EXTRACTORS
from indexing_schemes import SCHEMES
from name_similarity import DEFAULT_THRESHOLD
from normalize_engines import ENGINES
from ocr_providers import OCR_PROVIDERS
from settings import get_config, save_config

st.title("Config")

cfg = get_config()

with st.form("config_form"):
    st.subheader("Paths")
    input_image_path = st.text_input("Input image path", value=cfg.input_image_path)
    batch_output_path = st.text_input("Batch output path", value=cfg.batch_output_path)
    extract_structured = st.checkbox("Extract structured (DeepSeek)", value=cfg.extract_structured)
    if st.form_submit_button("Save"):
        updated = cfg.model_copy(update={"input_image_path": input_image_path, "batch_output_path": batch_output_path, "extract_structured": extract_structured})
        save_config(updated)
        st.rerun()

st.divider()
st.subheader("Preferences")

ocr_providers = list(OCR_PROVIDERS.keys())
extractors = list(EXTRACTORS.keys())
engine_ids = list(ENGINES.keys())
scheme_options = list(SCHEMES.keys())
rank_options = ["Total Spend", "Visit Count"]

with st.form("preferences_form"):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Models**")
        default_ocr_idx = ocr_providers.index(cfg.ocr_model) if cfg.ocr_model in ocr_providers else 0
        ocr_model = st.selectbox("OCR Model", ocr_providers, index=default_ocr_idx)
        default_workshop_ocr_idx = ocr_providers.index(cfg.workshop_ocr_model) if cfg.workshop_ocr_model in ocr_providers else 0
        workshop_ocr_model = st.selectbox("Workshop OCR Model", ocr_providers, index=default_workshop_ocr_idx)
        default_extractor_idx = extractors.index(cfg.extractor_model) if cfg.extractor_model in extractors else 0
        extractor_model = st.selectbox("Extractor Model", extractors, index=default_extractor_idx)
        default_workshop_extractor_idx = extractors.index(cfg.workshop_extractor_model) if cfg.workshop_extractor_model in extractors else 0
        workshop_extractor_model = st.selectbox("Workshop Extractor", extractors, index=default_workshop_extractor_idx)
    with col2:
        st.markdown("**Normalization**")
        default_engine_idx = engine_ids.index(cfg.normalize_engine) if cfg.normalize_engine in engine_ids else 0
        normalize_engine = st.selectbox("Normalize Engine", engine_ids, index=default_engine_idx, format_func=lambda x: ENGINES[x].label)
        step = DEFAULT_THRESHOLD / 20
        normalize_embedding_threshold = st.slider("Embedding distance threshold", min_value=step, max_value=step * 100, value=cfg.normalize_embedding_threshold, step=step)
        normalize_string_similarity = st.slider("String similarity (%)", min_value=50, max_value=100, value=cfg.normalize_string_similarity, step=1)
        st.markdown("**Other**")
        default_scheme_idx = scheme_options.index(cfg.indexing_scheme) if cfg.indexing_scheme in scheme_options else 0
        indexing_scheme = st.selectbox("Indexing scheme", scheme_options, index=default_scheme_idx)
        default_rank_idx = rank_options.index(cfg.dashboard_rank_by) if cfg.dashboard_rank_by in rank_options else 0
        dashboard_rank_by = st.selectbox("Dashboard rank by", rank_options, index=default_rank_idx)
    if st.form_submit_button("Save preferences"):
        updated = cfg.model_copy(update={
            "ocr_model": ocr_model,
            "workshop_ocr_model": workshop_ocr_model,
            "extractor_model": extractor_model,
            "workshop_extractor_model": workshop_extractor_model,
            "normalize_engine": normalize_engine,
            "normalize_embedding_threshold": normalize_embedding_threshold,
            "normalize_string_similarity": normalize_string_similarity,
            "indexing_scheme": indexing_scheme,
            "dashboard_rank_by": dashboard_rank_by,
        })
        save_config(updated)
        st.rerun()
