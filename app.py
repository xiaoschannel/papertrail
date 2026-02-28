from dotenv import load_dotenv

load_dotenv()
import streamlit as st

st.set_page_config(page_title="Papertrail", layout="wide")

pg = st.navigation({
    "Ingest": [
        st.Page("pages/ingest/file_index.py", title="File Index", icon=":material/folder_open:"),
        st.Page("pages/ingest/ocr.py", title="OCR", icon=":material/document_scanner:"),
        st.Page("pages/ingest/parse.py", title="Parse", icon=":material/label:"),
        st.Page("pages/ingest/review.py", title="Review", icon=":material/checklist:"),
        st.Page("pages/ingest/archive.py", title="Archive", icon=":material/archive:"),
    ],
    "Curate": [
        st.Page("pages/curate/marked_workshop.py", title="Marked Workshop", icon=":material/build:"),
        st.Page("pages/curate/dedupe.py", title="Dedupe", icon=":material/compare:"),
        st.Page("pages/curate/normalize.py", title="Normalize", icon=":material/edit:"),
    ],
    "Visualize": [
        st.Page("pages/visualize/dashboard.py", title="Dashboard", icon=":material/bar_chart:", url_path="dashboard"),
        st.Page("pages/visualize/merchant.py", title="Merchant Profile", icon=":material/store:", url_path="merchant"),
        st.Page("pages/visualize/receipt.py", title="Receipt Detail", icon=":material/receipt_long:", url_path="receipt"),
        st.Page("pages/visualize/timecapsule.py", title="Time Capsule", icon=":material/history:", url_path="timecapsule"),
    ],
    "Dev": [
        st.Page("pages/dev/ocr_experiment.py", title="OCR Experiment", icon=":material/science:"),
        st.Page("pages/dev/sanity_check.py", title="Sanity Check", icon=":material/vital_signs:"),
    ],
    "": [
        st.Page("pages/config.py", title="Config", icon=":material/settings:"),
    ],
})
pg.run()
