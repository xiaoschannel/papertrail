from dotenv import load_dotenv

load_dotenv()
import streamlit as st

st.set_page_config(page_title="Papertrail", layout="wide")

pg = st.navigation({
    "Ingest": [
        st.Page("pages/file_index.py", title="File Index", icon=":material/folder_open:"),
        st.Page("pages/ocr.py", title="OCR", icon=":material/document_scanner:"),
        st.Page("pages/parse.py", title="Parse", icon=":material/label:"),
        st.Page("pages/review.py", title="Review", icon=":material/checklist:"),
        st.Page("pages/archive.py", title="Archive", icon=":material/archive:"),
    ],
    "Curate": [
        st.Page("pages/marked_workshop.py", title="Marked Workshop", icon=":material/build:"),
        st.Page("pages/dedupe.py", title="Dedupe", icon=":material/compare:"),
        st.Page("pages/normalize.py", title="Normalize", icon=":material/edit:"),
    ],
    "Dev": [
        st.Page("pages/ocr_experiment.py", title="OCR Experiment", icon=":material/science:"),
        st.Page("pages/sanity_check.py", title="Sanity Check", icon=":material/vital_signs:"),
    ],
    "": [
        st.Page("pages/config.py", title="Config", icon=":material/settings:"),
    ],
})
pg.run()
