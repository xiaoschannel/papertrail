import os
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
import streamlit as st
from PIL import Image, ImageEnhance

from box_drawing import draw_all_boxes, draw_field_boxes
from data import (
    build_smart_match_history,
    load_decisions,
    load_extractions,
    load_reorganized_state,
    load_smart_match_cache,
    read_sidecar,
    save_smart_match_cache,
    write_sidecar,
)
from dedupe_candidates import get_receipts_in_week
from extraction import EXTRACTORS
from models import (
    VERDICT_COLORS,
    CorruptedResult,
    OcrResult,
    OtherResult,
    ReceiptResult,
    ReviewDecision,
    Sidecar,
    batch_serial_key,
    filename_to_batch_serial,
    load_scan_index,
)
from name_similarity import get_smart_match_candidates, quick_apply_label
from ocr_providers import OCR_PROVIDERS, run_ocr
from ocr_providers.deepseek import parse_grounding_output
from organize_utils import move_to_accepted_destination
from rules.cost_large_check import cost_large_check
from rules.cost_zero_check import cost_zero_check
from rules.currency_uncommon_check import currency_uncommon_check
from rules.date_check import date_check
from settings import get_config, update_config
from validation import HintRule, is_date_time_safe_for_archive

HINT_RULES: list[HintRule] = [date_check, cost_zero_check, cost_large_check, currency_uncommon_check]

st.title("Marked Workshop")

cfg = get_config()
batch_dir = cfg.batch_output_path

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
for fn, (sidecar, _path) in accepted_metadata.items():
    decisions[fn] = sidecar.review

# --- Navigation ---

if "workshop_idx" not in st.session_state or st.session_state.workshop_idx >= len(marked_files):
    st.session_state.workshop_idx = 0

nav_cols = st.columns([1, 1, 6])
leaving_ctx_key = f"ctx_{marked_files[st.session_state.workshop_idx]}"
if nav_cols[0].button("← Prev", disabled=(st.session_state.workshop_idx == 0)):
    st.session_state.pop(leaving_ctx_key, None)
    st.session_state.workshop_idx -= 1
    st.rerun()
if nav_cols[1].button("Next →", disabled=(st.session_state.workshop_idx >= len(marked_files) - 1)):
    st.session_state.pop(leaving_ctx_key, None)
    st.session_state.workshop_idx += 1
    st.rerun()

selected = marked_files[st.session_state.workshop_idx]
nav_cols[2].markdown(f"**{st.session_state.workshop_idx + 1} / {len(marked_files)}** — {selected}")

# --- Per-file session state ---

workshop_state_key = f"workshop_state_{selected}"
if workshop_state_key not in st.session_state:
    st.session_state[workshop_state_key] = {
        "rotation": 0,
        "ocr_text": None,
        "ocr_boxes": None,
        "extraction": None,
    }

workshop_state = st.session_state[workshop_state_key]

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
sidecar_ext = sidecar.extraction if sidecar else None
sidecar_ocr = sidecar.ocr if sidecar else None

extraction = workshop_state.get("extraction") or sidecar_ext

extraction_name = (
    (extraction.name or "Receipt") if isinstance(extraction, ReceiptResult)
    else (extraction.title or "Document") if isinstance(extraction, OtherResult)
    else "Corrupted"
)
extraction_phone = extraction.phone if isinstance(extraction, ReceiptResult) else ""

batch_extractions = load_extractions(output_path)
batch_decisions = load_decisions(output_path)
smart_cache_data = load_smart_match_cache(output_path)
workshop_smart_history = build_smart_match_history(
    batch_extractions, batch_decisions, smart_cache_data
)
workshop_candidates = get_smart_match_candidates(
    extraction_name, extraction_phone, workshop_smart_history
)
workshop_quick = [c for c in workshop_candidates if c.quick_apply]
workshop_confirmed_names = {r.confirmed for r in workshop_smart_history}
workshop_best_name_sim = max(
    (c.name_score for c in workshop_candidates), default=None
)


def _init_form_widgets(sel, ext, candidates):
    if isinstance(ext, ReceiptResult):
        nm = ext.name or "Receipt"
        st.session_state[f"wshop_doctype_{sel}"] = "receipt"
        st.session_state[f"wshop_date_{sel}"] = ext.date
        st.session_state[f"wshop_time_{sel}"] = ext.time
        cost = ext.cost
        st.session_state[f"wshop_cost_{sel}"] = str(int(cost)) if cost == int(cost) else str(cost)
        st.session_state[f"wshop_currency_{sel}"] = ext.currency
        st.session_state[f"wshop_jpy_{sel}"] = ext.currency.upper() == "JPY"
    elif isinstance(ext, OtherResult):
        nm = ext.title or "Document"
        st.session_state[f"wshop_doctype_{sel}"] = "other"
        st.session_state[f"wshop_date_{sel}"] = ext.date
        st.session_state[f"wshop_time_{sel}"] = ext.time
        st.session_state[f"wshop_cost_{sel}"] = "0"
        st.session_state[f"wshop_currency_{sel}"] = ""
        st.session_state[f"wshop_jpy_{sel}"] = False
    else:
        nm = "Corrupted"
        st.session_state[f"wshop_doctype_{sel}"] = "corrupted"
        st.session_state[f"wshop_date_{sel}"] = ""
        st.session_state[f"wshop_time_{sel}"] = ""
        st.session_state[f"wshop_cost_{sel}"] = "0"
        st.session_state[f"wshop_currency_{sel}"] = ""
        st.session_state[f"wshop_jpy_{sel}"] = False
    if candidates and abs(candidates[0].name_score - 1.0) < 1e-9:
        nm = candidates[0].confirmed_name
    st.session_state[f"wshop_name_{sel}"] = nm
    st.session_state.pop(f"wshop_sm_sel_{sel}", None)


if f"wshop_name_{selected}" not in st.session_state:
    _init_form_widgets(selected, extraction, workshop_candidates)


def _find_image(fn: str) -> Path | None:
    p = marked_dir / fn
    if p.exists():
        return p
    meta = accepted_metadata.get(fn)
    if meta:
        accepted_path = meta[1]
        if accepted_path:
            p = output_path / accepted_path
            if p.exists():
                return p
    p = tossed_dir / fn
    if p.exists():
        return p
    return None

# --- 4-column layout ---

orig_col, work_col, ocr_col, review_col = st.columns([1, 1, 1, 1])

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

    enhance = st.radio("Enhance", ["None", "CLAHE", "Contrast + Gamma", "Whiten background"], horizontal=True, key=f"enh_{selected}")
    if enhance == "CLAHE":
        clip = st.slider("Clip", 1.0, 10.0, 3.0, 0.5, key=f"clip_{selected}")
        grid = st.slider("Grid", 2, 16, 8, 1, key=f"grid_{selected}")
        work_arr = np.array(working_image)
        lab = cv2.cvtColor(work_arr, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        working_image = Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
    elif enhance == "Whiten background":
        l_min = st.slider("Lightness (min)", 128, 255, 200, 1, key=f"wb_L_{selected}")
        chroma_min = st.slider("Chroma (min)", 1, 80, 10, 1, key=f"wb_chroma_{selected}")
        work_arr = np.array(working_image)
        lab = cv2.cvtColor(work_arr, cv2.COLOR_RGB2LAB)
        L, a, b = lab[:, :, 0], lab[:, :, 1], lab[:, :, 2]
        light = L >= l_min
        chroma = np.maximum(np.abs(a.astype(np.int32) - 128), np.abs(b.astype(np.int32) - 128))
        colored = chroma >= chroma_min
        mask = light & colored
        lab[mask, 0] = 255
        lab[mask, 1] = 128
        lab[mask, 2] = 128
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
    active_boxes = workshop_state.get("ocr_boxes") or (sidecar_ocr.boxes if sidecar_ocr else None)
    field_sources = getattr(extraction, "field_sources", {}) if extraction else {}
    if active_boxes:
        if field_sources:
            st.image(draw_field_boxes(working_image, 1, active_boxes, field_sources), width="stretch")
        else:
            st.image(draw_all_boxes(working_image, active_boxes), width="stretch")
    else:
        st.image(working_image, width="stretch")

    ocr_providers = list(OCR_PROVIDERS.keys())
    default_workshop_ocr_idx = ocr_providers.index(cfg.workshop_ocr_model) if cfg.workshop_ocr_model in ocr_providers else 0

    def _save_workshop_ocr():
        update_config(workshop_ocr_model=st.session_state["workshop_ocr"])

    extractors = list(EXTRACTORS.keys())
    default_workshop_extractor_idx = extractors.index(cfg.workshop_extractor_model) if cfg.workshop_extractor_model in extractors else 0

    def _save_workshop_extractor():
        update_config(workshop_extractor_model=st.session_state["workshop_extractor"])

    ocr_provider = st.selectbox("OCR", ocr_providers, index=default_workshop_ocr_idx, key="workshop_ocr", on_change=_save_workshop_ocr)
    extractor_name = st.selectbox("Extractor", extractors, index=default_workshop_extractor_idx, key="workshop_extractor", on_change=_save_workshop_extractor)

    if st.button("Reprocess", type="primary", width="stretch"):
        fd, tmp_str = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        tmp_path = Path(tmp_str)
        working_image.save(tmp_path)
        extract_structured = cfg.extract_structured
        with st.spinner("Running OCR..."):
            plain_raw = run_ocr(tmp_path, provider=ocr_provider, structured=False)
        workshop_state["ocr_text"] = plain_raw
        if extract_structured:
            with st.spinner("Running structured OCR..."):
                structured_raw = run_ocr(tmp_path, provider=ocr_provider, structured=True)
            boxes = parse_grounding_output(structured_raw)
            workshop_state["ocr_boxes"] = boxes
            if boxes:
                annotated_lines = ["--- Page 1 ---"]
                for idx, box in enumerate(boxes):
                    annotated_lines.append(f"[P1-BOX-{idx}] {box.text}")
                extractor_input = "\n".join(annotated_lines)
                has_boxes = True
            else:
                extractor_input = plain_raw
                has_boxes = False
        else:
            workshop_state["ocr_boxes"] = None
            extractor_input = plain_raw
            has_boxes = False
        tmp_path.unlink()
        extract_fn = EXTRACTORS[extractor_name]
        with st.spinner("Extracting..."):
            new_ext = extract_fn(
                extractor_input,
                has_boxes=has_boxes,
                custom_instruction=cfg.parse_custom_instruction,
            )
        workshop_state["extraction"] = new_ext
        reprocess_name = (
            (new_ext.name or "Receipt") if isinstance(new_ext, ReceiptResult)
            else (new_ext.title or "Document") if isinstance(new_ext, OtherResult)
            else "Corrupted"
        )
        reprocess_phone = new_ext.phone if isinstance(new_ext, ReceiptResult) else ""
        new_candidates = get_smart_match_candidates(
            reprocess_name, reprocess_phone, workshop_smart_history
        )
        _init_form_widgets(selected, new_ext, new_candidates)
        st.rerun()

# Column 3: OCR Text
ocr_text = workshop_state.get("ocr_text") or (sidecar_ocr.markdown if sidecar_ocr else "")
with ocr_col:
    st.markdown("**OCR Text**")
    if ocr_text:
        st.markdown(ocr_text.replace("\n", "  \n"), unsafe_allow_html=True)
    else:
        st.caption("(Run Reprocess for OCR)")

# Column 4: Review (Smart Match, form, validation, Accept/Toss)
with review_col:
    st.markdown("**Review**")

    ws_name_key = f"wshop_name_{selected}"
    ws_sel_key = f"wshop_sm_sel_{selected}"

    if workshop_quick:
        wsq_cols = st.columns(min(3, len(workshop_quick)))
        for i, cand in enumerate(workshop_quick):
            with wsq_cols[i % len(wsq_cols)]:
                if st.button(
                    quick_apply_label(cand),
                    key=f"wqa_{selected}_{i}_{cand.confirmed_name}",
                    width="stretch",
                ):
                    st.session_state[ws_name_key] = cand.confirmed_name
                    st.rerun()

    ws_opts = [""] + [c.confirmed_name for c in workshop_candidates]
    ws_best_label = f" — {workshop_best_name_sim:.0%}" if workshop_best_name_sim is not None else ""
    ws_default_idx = (
        1
        if workshop_candidates
        and abs(workshop_candidates[0].name_score - 1.0) < 1e-9
        else 0
    )

    def _wshop_sync_smart_pick():
        v = st.session_state.get(ws_sel_key, "")
        if v:
            st.session_state[ws_name_key] = v

    st.selectbox(
        f"Smart Match ({len(workshop_candidates)}){ws_best_label}",
        ws_opts,
        index=ws_default_idx,
        key=ws_sel_key,
        on_change=_wshop_sync_smart_pick,
    )

    doc_type_options = ["receipt", "other", "corrupted"]
    doc_type = st.radio(
        "Type",
        doc_type_options,
        horizontal=True,
        key=f"wshop_doctype_{selected}",
    )
    name = st.text_input("Name", key=ws_name_key)

    PLACEHOLDER_NAMES = {"Receipt", "Document", "Corrupted"}
    if name.strip() and name not in PLACEHOLDER_NAMES:
        if name in workshop_confirmed_names:
            st.markdown(
                '<span style="color:#28a745;font-weight:600;">Name previously approved</span>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<span style="color:#ffc107;font-weight:600;">Name not seen in previous reviews</span>',
                unsafe_allow_html=True,
            )

    dt_cols = st.columns(2)
    date_val = dt_cols[0].text_input("Date", key=f"wshop_date_{selected}")
    time_val = dt_cols[1].text_input("Time", key=f"wshop_time_{selected}")

    if doc_type == "receipt":
        cost_cols = st.columns([2, 1, 1])
        cost_str = cost_cols[0].text_input("Cost", key=f"wshop_cost_{selected}")
        currency_val = cost_cols[1].text_input("Currency", key=f"wshop_currency_{selected}")
        jpy_checked = cost_cols[2].checkbox("JPY", key=f"wshop_jpy_{selected}")
        st.markdown(
            "<style>[data-testid='stHorizontalBlock'] [data-testid='stCheckbox']{padding-top:2.1rem;}</style>",
            unsafe_allow_html=True,
        )
    else:
        cost_str = "0"
        currency_val = ""
        jpy_checked = False

    try:
        parsed_cost_live = float(cost_str)
    except ValueError:
        parsed_cost_live = 0.0
    final_currency_live = "JPY" if jpy_checked else currency_val
    if doc_type == "receipt":
        live_ext = ReceiptResult(
            document_type="receipt",
            language=extraction.language if isinstance(extraction, ReceiptResult) else "",
            date=date_val,
            time=time_val,
            name=name,
            phone=extraction.phone if isinstance(extraction, ReceiptResult) else "",
            currency=final_currency_live,
            address=extraction.address if isinstance(extraction, ReceiptResult) else "",
            items=extraction.items if isinstance(extraction, ReceiptResult) else [],
            cost=parsed_cost_live,
        )
    elif doc_type == "other":
        live_ext = OtherResult(
            document_type="other",
            language=extraction.language if isinstance(extraction, OtherResult) else "",
            date=date_val,
            time=time_val,
            title=name,
        )
    else:
        live_ext = CorruptedResult(document_type="corrupted")
    for rule in HINT_RULES:
        for result in rule(live_ext):
            if result.color:
                st.markdown(
                    f'<span style="color:{result.color};font-weight:600;">{result.message}</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"**{result.message}**")

    btn_cols = st.columns(2)
    btn_accept = btn_cols[0].button("Accept", type="primary", width="stretch", key=f"accept_{selected}")
    btn_toss = btn_cols[1].button("Toss", width="stretch", key=f"toss_{selected}")

    if btn_accept or btn_toss:
        verdict = "accepted" if btn_accept else "tossed"
        try:
            parsed_cost = float(cost_str)
        except ValueError:
            parsed_cost = 0.0
        final_currency = "JPY" if jpy_checked else currency_val

        if btn_accept and doc_type == "receipt" and (not cost_str.strip() or not final_currency):
            missing = []
            if not cost_str.strip():
                missing.append("cost")
            if not final_currency:
                missing.append("currency")
            st.error(f"Receipt requires: {', '.join(missing)}")
        elif btn_accept:
            safe, err = is_date_time_safe_for_archive(date_val, time_val)
            if not safe:
                st.error(err)
            else:
                decision = ReviewDecision(
                    verdict="accepted",
                    document_type=doc_type,
                    name=name,
                    date=date_val,
                    time=time_val,
                    cost=parsed_cost,
                    currency=final_currency,
                    comment=sidecar.review.comment if sidecar else "",
                )
                dst = move_to_accepted_destination(output_path, selected, marked_dir / selected, decision)
                dest_rel = dst.relative_to(output_path).as_posix()
                if workshop_state.get("ocr_text"):
                    final_ocr = OcrResult(markdown=workshop_state["ocr_text"], boxes=workshop_state.get("ocr_boxes"))
                else:
                    final_ocr = sidecar_ocr
                final_ext = workshop_state.get("extraction") or sidecar_ext
                new_sidecar = Sidecar(
                    original_filename=selected,
                    batch_id=sidecar.batch_id if sidecar else None,
                    serial=sidecar.serial if sidecar else None,
                    review=decision,
                    ocr=final_ocr,
                    extraction=final_ext,
                )
                write_sidecar(dst, new_sidecar)
                smart_match_cache = load_smart_match_cache(output_path)
                if isinstance(final_ext, ReceiptResult):
                    extracted_name = final_ext.name
                    extracted_phone = final_ext.phone
                elif isinstance(final_ext, OtherResult):
                    extracted_name = final_ext.title
                    extracted_phone = ""
                else:
                    extracted_name = ""
                    extracted_phone = ""
                batch_id, serial = new_sidecar.batch_id, new_sidecar.serial
                cache_key = batch_serial_key(batch_id, serial) if batch_id is not None and serial is not None else selected
                smart_match_cache[cache_key] = {
                    "extracted": extracted_name,
                    "confirmed": decision.name,
                    "extracted_phone": extracted_phone,
                }
                save_smart_match_cache(output_path, smart_match_cache)
                st.session_state.pop(workshop_state_key, None)
                st.success(f"Accepted → {dest_rel}")
                st.rerun()
        else:
            tossed_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(marked_dir / selected), str(tossed_dir / selected))
            marked_sidecar = (marked_dir / selected).with_suffix(".json")
            if marked_sidecar.exists():
                shutil.move(str(marked_sidecar), str((tossed_dir / selected).with_suffix(".json")))
            st.session_state.pop(workshop_state_key, None)
            st.success(f"Tossed → tossed/{selected}")
            st.rerun()

# --- Context by time (below columns) ---

st.divider()
st.markdown("**Context by time**")

week_receipts: list[str] = []
if doc_type == "receipt" and date_val and time_val:
    week_receipts = get_receipts_in_week(
        date_val, time_val, decisions,
        include_fn=selected, include_date=date_val, include_time=time_val,
    )

if week_receipts:
    window_size = 5
    ctx_key = f"ctx_{selected}"
    try:
        current_idx = week_receipts.index(selected)
    except ValueError:
        current_idx = 0
    if ctx_key not in st.session_state:
        st.session_state[ctx_key] = max(0, min(current_idx - 2, len(week_receipts) - window_size))
    ctx_start = st.session_state[ctx_key]
    ctx_end = min(len(week_receipts), ctx_start + window_size)
    adj_window = week_receipts[ctx_start:ctx_end]

    ctx_nav = st.columns([1, 1, 6])
    if ctx_nav[0].button("◀ Prev", key="ctx_prev", disabled=(ctx_start == 0)):
        st.session_state[ctx_key] = max(0, ctx_start - 1)
        st.rerun()
    if ctx_nav[1].button("Next ▶", key="ctx_next", disabled=(ctx_end >= len(week_receipts))):
        st.session_state[ctx_key] = ctx_start + 1
        st.rerun()
    ctx_nav[2].caption(f"Week around this receipt — {ctx_start + 1}–{ctx_end} of {len(week_receipts)}")

    img_cols = st.columns(window_size)
    for i, adj_fn in enumerate(adj_window):
        with img_cols[i]:
            if adj_fn in tossed_fns:
                verdict = "tossed"
            elif (marked_dir / adj_fn).exists():
                verdict = "marked"
            elif adj_fn in accepted_metadata:
                verdict = "accepted"
            else:
                verdict = ""
            verdict_color = VERDICT_COLORS.get(verdict, "#999")
            verdict_label = verdict.capitalize() if verdict else "?"
            adj_dec = decisions.get(adj_fn)
            detail = f" — {adj_dec.name} {adj_dec.cost} {adj_dec.currency}" if adj_dec else ""
            if adj_fn == selected:
                st.markdown(f'**► {adj_fn}** <span style="color:{verdict_color};font-weight:600;">{verdict_label}</span>{detail}', unsafe_allow_html=True)
            else:
                st.markdown(f'{adj_fn} <span style="color:{verdict_color};">{verdict_label}</span>{detail}', unsafe_allow_html=True)
            found = _find_image(adj_fn)
            if found:
                st.image(str(found), width="stretch")
            else:
                st.caption("(not found)")
else:
    if doc_type != "receipt" or not date_val or not time_val:
        st.caption("Set date and time to see receipts in the surrounding week")
    else:
        st.caption("No receipts in this week")

# --- Same-batch viewer (below columns) ---

st.divider()
st.markdown("**Same Batch**")

batch_files_map: dict[int, list[str]] = {}
fn_to_bs: dict[str, tuple[int, int]] = {}
batches_file = output_path / "batches.json"
if batches_file.exists():
    scan_index = load_scan_index(output_path)
    fn_to_bs = filename_to_batch_serial(scan_index)
    for b in scan_index.batches:
        batch_files_map[b.batch_id] = [b.files[s] for s in sorted(b.files)]

batch_id = fn_to_bs.get(selected, (None, None))[0]
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
