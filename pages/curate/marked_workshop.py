import os
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from PIL import Image, ImageEnhance

from data import (
    load_name_cache,
    load_name_normalizations,
    load_reorganized_state,
    read_sidecar,
    save_name_cache,
    write_sidecar,
)
from dedupe_candidates import find_adjacent_documents
from extraction import EXTRACTORS
from models import (
    VERDICT_COLORS,
    DocumentExtractionAdapter,
    OtherResult,
    ReceiptResult,
    ReviewDecision,
    load_scan_index,
)
from name_similarity import ensure_embeddings, find_similar_names
from ocr_providers import OCR_PROVIDERS, run_ocr
from organize_utils import resolve_single_accepted_destination
from settings import get_config

st.title("Marked Workshop")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)

marked_dir = output_path / "marked"
tossed_dir = output_path / "tossed"

marked_files = sorted(
    p.name for p in marked_dir.iterdir()
    if p.is_file() and p.suffix.lower() != ".json"
) if marked_dir.exists() else []
tossed_fns, accepted_metadata = load_reorganized_state(output_path)

if not marked_files:
    st.success("No marked files to process.")
    st.stop()

st.metric("Marked files", len(marked_files))

decisions: dict[str, ReviewDecision] = {}
for fn, entry in accepted_metadata.items():
    review = entry.get("review")
    if review:
        decisions[fn] = ReviewDecision(**review)

# --- Navigation ---

if "workshop_idx" not in st.session_state or st.session_state.workshop_idx >= len(marked_files):
    st.session_state.workshop_idx = 0

nav_cols = st.columns([1, 1, 6])
if nav_cols[0].button("← Prev", disabled=(st.session_state.workshop_idx == 0)):
    st.session_state.workshop_idx -= 1
    st.rerun()
if nav_cols[1].button("Next →", disabled=(st.session_state.workshop_idx >= len(marked_files) - 1)):
    st.session_state.workshop_idx += 1
    st.rerun()

selected = marked_files[st.session_state.workshop_idx]
nav_cols[2].markdown(f"**{st.session_state.workshop_idx + 1} / {len(marked_files)}** — {selected}")

# --- Per-file session state ---

ws_key = f"ws_{selected}"
if ws_key not in st.session_state:
    st.session_state[ws_key] = {
        "rotation": 0,
        "ocr_text": None,
        "extraction": None,
        "working_values": None,
    }

ws = st.session_state[ws_key]

# --- Load and transform image ---

img_path = marked_dir / selected
original = Image.open(str(img_path)).convert("RGB")

ROTATION_MAP = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}

# --- Sidecar data ---

sidecar = read_sidecar(marked_dir / selected)
sidecar_ext = None
if sidecar.get("extraction"):
    sidecar_ext = DocumentExtractionAdapter.validate_python(sidecar["extraction"])
sidecar_ocr_text = sidecar.get("ocr", {}).get("markdown", "")

ext = ws.get("extraction") or sidecar_ext

if isinstance(ext, ReceiptResult):
    default_doc_type = "receipt"
    default_name = ext.name or "Receipt"
    default_date = ext.date
    default_time = ext.time
    default_cost = ext.cost
    default_currency = ext.currency
elif isinstance(ext, OtherResult):
    default_doc_type = "other"
    default_name = ext.title or "Document"
    default_date = ext.date
    default_time = ext.time
    default_cost = 0.0
    default_currency = ""
else:
    default_doc_type = "corrupted"
    default_name = "Corrupted"
    default_date = ""
    default_time = ""
    default_cost = 0.0
    default_currency = ""

wv = ws.get("working_values")
if wv:
    default_doc_type = wv["document_type"]
    default_name = wv["name"]
    default_date = wv["date"]
    default_time = wv["time"]
    default_cost = wv["cost"]
    default_currency = wv["currency"]

def _find_image(fn: str) -> Path | None:
    p = marked_dir / fn
    if p.exists():
        return p
    accepted_path = accepted_metadata.get(fn, {}).get("_path", "")
    if accepted_path:
        p = output_path / accepted_path
        if p.exists():
            return p
    p = tossed_dir / fn
    if p.exists():
        return p
    return None

# --- 4-column layout ---

orig_col, work_col, form_col, context_col = st.columns([1, 1, 1, 1])

# Column 1: Original image
with orig_col:
    st.markdown("**Original**")
    st.image(original, width="stretch")
    working_image = original

    orientation = st.radio("Orientation", ["↑", "←", "→", "↓"], horizontal=True, key=f"ori_{selected}")
    if orientation == "←":
        working_image = original.transpose(ROTATION_MAP[270])
    elif orientation == "→":
        working_image = original.transpose(ROTATION_MAP[90])
    elif orientation == "↓":
        working_image = original.transpose(ROTATION_MAP[180])

    enhance = st.radio("Enhance", ["None", "CLAHE", "Contrast + Gamma"], horizontal=True, key=f"enh_{selected}")
    if enhance == "CLAHE":
        clip = st.slider("Clip", 1.0, 10.0, 3.0, 0.5, key=f"clip_{selected}")
        grid = st.slider("Grid", 2, 16, 8, 1, key=f"grid_{selected}")
        work_arr = np.array(working_image)
        lab = cv2.cvtColor(work_arr, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        working_image = Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
    elif enhance == "Contrast + Gamma":
        contrast_val = st.slider("Contrast", 0.5, 3.0, 2.5, 0.1, key=f"ctr_{selected}")
        gamma_val = st.slider("Gamma", 0.2, 3.0, 0.5, 0.1, key=f"gam_{selected}")
        working_image = ImageEnhance.Contrast(working_image).enhance(contrast_val)
        if gamma_val != 1.0:
            lut = [int(((i / 255.0) ** (1.0 / gamma_val)) * 255) for i in range(256)]
            working_image = working_image.point(lut * 3)

# Column 2: Working image + rotate/enhance/reprocess
with work_col:
    st.markdown("**Working Image**")
    st.image(working_image, width="stretch")

    ocr_provider = st.selectbox("OCR", list(OCR_PROVIDERS.keys()), key="workshop_ocr")
    extractor_name = st.selectbox("Extractor", list(EXTRACTORS.keys()), key="workshop_extractor")

    if st.button("Reprocess", type="primary", width="stretch"):
        fd, tmp_str = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        tmp_path = Path(tmp_str)
        working_image.save(tmp_path)
        with st.spinner("Running OCR..."):
            new_ocr = run_ocr(tmp_path, provider=ocr_provider)
        tmp_path.unlink()
        ws["ocr_text"] = new_ocr
        extract_fn = EXTRACTORS[extractor_name]
        with st.spinner("Extracting..."):
            new_ext = extract_fn(new_ocr)
        ws["extraction"] = new_ext
        if isinstance(new_ext, ReceiptResult):
            ws["working_values"] = {
                "document_type": "receipt",
                "name": new_ext.name or "Receipt",
                "date": new_ext.date,
                "time": new_ext.time,
                "cost": new_ext.cost,
                "currency": new_ext.currency,
            }
        elif isinstance(new_ext, OtherResult):
            ws["working_values"] = {
                "document_type": "other",
                "name": new_ext.title or "Document",
                "date": new_ext.date,
                "time": new_ext.time,
                "cost": 0.0,
                "currency": "",
            }
        else:
            ws["working_values"] = None
        st.rerun()

    ocr_text = ws.get("ocr_text") or sidecar_ocr_text
    if ocr_text:
        with st.expander("OCR Text"):
            st.markdown(ocr_text.replace("\n", "  \n"), unsafe_allow_html=True)

# Column 3: Review form with Set
with form_col:
    st.markdown("**Review**")

    doc_type_options = ["receipt", "other", "corrupted"]
    with st.form(key=f"wf_{selected}"):
        doc_type = st.radio(
            "Type",
            doc_type_options,
            index=doc_type_options.index(default_doc_type),
            horizontal=True,
        )
        name = st.text_input("Name", value=default_name)

        dt_cols = st.columns(2)
        date_val = dt_cols[0].text_input("Date", value=default_date)
        time_val = dt_cols[1].text_input("Time", value=default_time)

        if default_doc_type == "receipt":
            cost_cols = st.columns([2, 1, 1])
            cost_display = str(int(default_cost)) if default_cost == int(default_cost) else str(default_cost)
            cost_str = cost_cols[0].text_input("Cost", value=cost_display)
            currency_val = cost_cols[1].text_input("Currency", value=default_currency)
            jpy_checked = cost_cols[2].checkbox("JPY", value=(default_currency.upper() == "JPY"))
        else:
            cost_str = "0"
            currency_val = ""
            jpy_checked = False

        st.markdown(
            "<style>[data-testid='stHorizontalBlock'] [data-testid='stCheckbox']{padding-top:2.1rem;}</style>",
            unsafe_allow_html=True,
        )
        set_clicked = st.form_submit_button("Set — update context below", type="primary", width="stretch")

    if set_clicked:
        parsed_cost = 0.0
        try:
            parsed_cost = float(cost_str)
        except ValueError:
            pass
        final_currency = "JPY" if jpy_checked else currency_val
        ws["working_values"] = {
            "document_type": doc_type,
            "name": name,
            "date": date_val,
            "time": time_val,
            "cost": parsed_cost,
            "currency": final_currency,
        }
        st.rerun()

# Column 4: Context assistants
with context_col:
    st.markdown("**Context**")

    if wv:
        st.markdown("**Time-adjacent documents**")
        if wv["document_type"] == "receipt" and wv["date"] and wv["time"]:
            adjacent = find_adjacent_documents(
                wv["date"], wv["time"], wv["cost"],
                decisions, exclude_fn=selected,
            )
            if adjacent:
                for adj_fn in adjacent[:5]:
                    adj_dec = decisions[adj_fn]
                    st.markdown(f"- {adj_fn}: {adj_dec.name} — {adj_dec.cost} {adj_dec.currency}")
                with st.expander("Show images"):
                    for adj_fn in adjacent[:5]:
                        found = _find_image(adj_fn)
                        if found:
                            st.caption(adj_fn)
                            st.image(str(found), width="stretch")
            else:
                st.caption("No adjacent documents found")

        st.markdown("**Similar names**")
        decision_names = {dec.name for dec in decisions.values() if dec.verdict != "tossed"}
        normalizations = load_name_normalizations(output_path)
        canonical_names = set(normalizations.values())
        all_known_names = sorted(decision_names | canonical_names | {wv["name"]})

        if wv["name"] and all_known_names:
            if wv["name"] in decision_names | canonical_names:
                st.success("Name previously approved!")

            else:
                cached_names, cached_matrix = ensure_embeddings(output_path, all_known_names)
                similar = find_similar_names(
                    wv["name"], all_known_names, cached_names, cached_matrix,
                )
                if similar:
                    for sim_name, dist in similar:
                        col_n, col_b = st.columns([3, 1])
                        col_n.text(f"{sim_name} ({dist:.2f})")
                        if col_b.button("Use", key=f"use_{sim_name}"):
                            ws["working_values"]["name"] = sim_name
                            st.rerun()
                else:
                    st.caption("No similar names found")
    else:
        st.caption("Set values to see adjacent documents and name suggestions")

# --- Same-batch viewer (below columns) ---

st.divider()
st.markdown("**Same Batch**")

filename_to_batch: dict[str, int] = {}
batch_files_map: dict[int, list[str]] = {}
batches_file = output_path / "batches.json"
if batches_file.exists():
    scan_index, filename_to_batch = load_scan_index(output_path)
    for b in scan_index.batches:
        batch_files_map[b.batch_id] = [b.files[s] for s in sorted(b.files)]

batch_id = filename_to_batch.get(selected)
if batch_id is not None and batch_id in batch_files_map:
    batch_filenames = batch_files_map[batch_id]
    sel_idx = batch_filenames.index(selected) if selected in batch_filenames else 0

    bw_key = f"bw_{selected}"
    if bw_key not in st.session_state:
        st.session_state[bw_key] = max(0, sel_idx - 2)

    window_start = st.session_state[bw_key]
    window_end = min(len(batch_filenames), window_start + 5)
    window = batch_filenames[window_start:window_end]

    batch_nav = st.columns([1, 1, 6])
    if batch_nav[0].button("◀ Prev", key="bp", disabled=(window_start == 0)):
        st.session_state[bw_key] = max(0, window_start - 1)
        st.rerun()
    if batch_nav[1].button("Next ▶", key="bn", disabled=(window_end >= len(batch_filenames))):
        st.session_state[bw_key] = window_start + 1
        st.rerun()
    batch_nav[2].caption(f"Batch {batch_id} — showing {window_start + 1}–{window_end} of {len(batch_filenames)}")

    img_cols = st.columns(5)
    for i, bfn in enumerate(window):
        with img_cols[i]:
            if bfn in tossed_fns:
                verdict = "tossed"
            elif (marked_dir / bfn).exists():
                verdict = "marked"
            elif bfn in accepted_metadata:
                verdict = "accepted"
            else:
                verdict = ""
            verdict_color = VERDICT_COLORS.get(verdict, "#999")
            verdict_label = verdict.capitalize() if verdict else "?"
            if bfn == selected:
                st.markdown(f'**► {bfn}** <span style="color:{verdict_color};font-weight:600;">{verdict_label}</span>', unsafe_allow_html=True)
            else:
                st.markdown(f'{bfn} <span style="color:{verdict_color};">{verdict_label}</span>', unsafe_allow_html=True)
            found = _find_image(bfn)

            if found:
                st.image(str(found), width="stretch")
            else:
                st.caption("(not found)")
else:
    st.caption("No batch info available for this file")

# --- Finalize ---

st.divider()
st.subheader("Finalize")

fin_cols = st.columns(2)
accept_clicked = fin_cols[0].button("Accept", type="primary", width="stretch", disabled=(not wv))
toss_clicked = fin_cols[1].button("Toss", width="stretch")

if not wv and not toss_clicked:
    fin_cols[0].caption("Set values to enable Accept")

if accept_clicked:
    parsed_cost = wv["cost"]
    final_currency = wv["currency"]

    if wv["document_type"] == "receipt" and (not parsed_cost or not final_currency):
        missing = []
        if not parsed_cost:
            missing.append("cost")
        if not final_currency:
            missing.append("currency")
        st.error(f"Receipt requires: {', '.join(missing)}")
    else:
        dec = ReviewDecision(
            verdict="accepted",
            document_type=wv["document_type"],
            name=wv["name"],
            date=wv["date"],
            time=wv["time"],
            cost=parsed_cost,
            currency=final_currency,
        )

        dest_rel = resolve_single_accepted_destination(output_path, selected, dec)
        dst = output_path / dest_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(marked_dir / selected), str(dst))
        marked_sidecar = (marked_dir / selected).with_suffix(".json")
        if marked_sidecar.exists():
            marked_sidecar.unlink()

        entry: dict = {
            "original_filename": selected,
            "batch_id": sidecar.get("batch_id"),
            "serial": sidecar.get("serial"),
            "review": dec.model_dump(),
        }
        final_ocr = sidecar.get("ocr")
        if ws.get("ocr_text"):
            entry["ocr"] = {"markdown": ws["ocr_text"]}
        elif final_ocr:
            entry["ocr"] = final_ocr
        final_ext = ws.get("extraction") or sidecar_ext
        if final_ext:
            entry["extraction"] = final_ext.model_dump()
        write_sidecar(dst, entry)

        # Update name cache
        name_cache = load_name_cache(output_path)
        if isinstance(final_ext, ReceiptResult):
            extracted_name = final_ext.name
        elif isinstance(final_ext, OtherResult):
            extracted_name = final_ext.title
        else:
            extracted_name = ""
        name_cache[selected] = {"extracted": extracted_name, "confirmed": dec.name}
        save_name_cache(output_path, name_cache)

        # Clean session state for this file
        st.session_state.pop(ws_key, None)
        st.success(f"Accepted → {dest_rel}")
        st.rerun()

if toss_clicked:
    tossed_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(marked_dir / selected), str(tossed_dir / selected))
    marked_sidecar = (marked_dir / selected).with_suffix(".json")
    if marked_sidecar.exists():
        shutil.move(str(marked_sidecar), str((tossed_dir / selected).with_suffix(".json")))

    st.session_state.pop(ws_key, None)
    st.success(f"Tossed → tossed/{selected}")
    st.rerun()
