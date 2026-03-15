from pathlib import Path

import streamlit as st

from data import (
    build_document_index,
    load_decisions,
    load_extractions,
    load_name_cache,
    load_ocr_results,
    save_decisions,
)
from models import (
    VERDICT_COLORS,
    VERDICT_LABELS,
    CorruptedResult,
    DocumentKey,
    OtherResult,
    ReceiptResult,
    ReviewDecision,
    batch_serial_key,
    iter_indexed_files,
    load_scan_index,
)
from name_similarity import get_smart_match_suggestions
from rules.cost_large_check import cost_large_check
from rules.cost_zero_check import cost_zero_check
from rules.currency_uncommon_check import currency_uncommon_check
from rules.date_check import date_check
from settings import get_config
from validation import HintRule, is_date_time_safe_for_archive

st.title("Review")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")
image_dir = cfg.get("input_image_path", "")

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)
index_file = output_path / "batches.json"
if not index_file.exists():
    st.info("Run File Index first to create batches.json.")
    st.stop()

scan_index = load_scan_index(output_path)
indexed_keys = {batch_serial_key(bid, ser) for bid, ser, _ in iter_indexed_files(scan_index, include_archived=False)}
key_to_filename = {batch_serial_key(bid, ser): fn for bid, ser, fn in iter_indexed_files(scan_index, include_archived=False)}
loaded = load_ocr_results(output_path)
ocr_by_key = {k: r.markdown for k, r in loaded.items() if r.succeeded}
extractions = load_extractions(output_path)
decisions = load_decisions(output_path)

index = build_document_index(output_path, indexed_keys)
extracted_doc_keys = [k for k in extractions]
if not extracted_doc_keys:
    st.info("No extractions yet. Run Parse first.")
    st.stop()

HINT_RULES: list[HintRule] = [date_check, cost_zero_check, cost_large_check, currency_uncommon_check]

name_pairs: dict[str, tuple[str, str]] = {}
for doc_key, dec in decisions.items():
    if dec.verdict == "accepted" and doc_key in extractions:
        ext_i = extractions[doc_key]
        if isinstance(ext_i, ReceiptResult):
            name_pairs[doc_key] = (ext_i.name, dec.name)
        elif isinstance(ext_i, OtherResult):
            name_pairs[doc_key] = (ext_i.title, dec.name)
name_cache = load_name_cache(output_path)
for doc_key, entry in name_cache.items():
    name_pairs[doc_key] = (entry["extracted"], entry["confirmed"])


def _review_sort_key(doc_key: str):
    ext = extractions[doc_key]
    if isinstance(ext, ReceiptResult):
        return (2, ext.name.lower())
    elif isinstance(ext, OtherResult):
        return (1, (ext.title or "").lower())
    return (0, "")


to_review = sorted((dk for dk in extracted_doc_keys if dk not in decisions), key=_review_sort_key)
total = len(extracted_doc_keys)

verdict_counts = {v: 0 for v in VERDICT_LABELS}
for d in decisions.values():
    verdict_counts[d.verdict] = verdict_counts.get(d.verdict, 0) + 1

segments = [(len(to_review), "#6c757d", f"Review: {len(to_review)}")]
for verdict, label in VERDICT_LABELS.items():
    count = verdict_counts[verdict]
    segments.append((count, VERDICT_COLORS[verdict], f"{label}: {count}"))
bar_parts = []
for count, color, label in segments:
    if count > 0:
        pct = count / total * 100
        bar_parts.append(
            f'<div style="width:{pct}%;background:{color};color:white;'
            f"display:flex;align-items:center;justify-content:center;"
            f'font-size:12px;font-weight:600;min-width:fit-content;padding:0 8px;">'
            f"{label}</div>"
        )
bar_html = (
    '<div style="display:flex;height:28px;border-radius:6px;overflow:hidden;margin-bottom:16px;">'
    + "".join(bar_parts)
    + "</div>"
)
st.markdown(bar_html, unsafe_allow_html=True)

if st.button("Clear all reviews"):
    @st.dialog("Confirm Clear All Reviews")
    def confirm_clear_reviews():
        st.warning("This will clear all review decisions. This cannot be undone.")
        if st.button("Confirm", type="primary"):
            decisions.clear()
            save_decisions(output_path, decisions)
            st.rerun()

    confirm_clear_reviews()

if not to_review:
    st.success("All items reviewed!")
    st.stop()

if "review_idx" not in st.session_state or st.session_state.review_idx >= len(to_review):
    st.session_state.review_idx = 0

selected = to_review[st.session_state.review_idx]
doc_key = DocumentKey.parse(selected) or DocumentKey.from_group([selected])
selected_keys = index.keys_for_doc(doc_key)
img_dir = Path(image_dir) if image_dir else None

nav_cols = st.columns([1, 1, 6])
if nav_cols[0].button("← Prev", disabled=(st.session_state.review_idx == 0)):
    st.session_state.review_idx -= 1
    st.rerun()
if nav_cols[1].button("Next →", disabled=(st.session_state.review_idx >= len(to_review) - 1)):
    st.session_state.review_idx += 1
    st.rerun()
nav_cols[2].markdown(f"**{st.session_state.review_idx + 1} / {len(to_review)}** — {selected}")

ext = extractions[selected]

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

suggestions, best_sim = get_smart_match_suggestions(default_name, name_pairs)

ocr_col, image_col, result_col = st.columns([1, 1, 2])

with ocr_col:
    st.markdown("**OCR Output**")
    ocr_text = index.concat_ocr(doc_key, ocr_by_key)
    st.markdown(ocr_text.replace("\n", "  \n"), unsafe_allow_html=True)

with image_col:
    if img_dir:
        for i, k in enumerate(selected_keys):
            fn = key_to_filename.get(k, k)
            img_path = img_dir / fn
            if img_path.exists():
                st.image(str(img_path), caption=f"Page {i + 1}: {fn}", width="stretch")

with result_col:
    best_label = f" — {best_sim:.0%}" if best_sim is not None else ""
    smart_match_index = 1 if best_sim == 1.0 and suggestions else 0
    smart_match = st.selectbox(f"Smart Match ({len(suggestions)}){best_label}", [""] + suggestions, index=smart_match_index)
    effective_name = smart_match if smart_match else default_name

    doc_type_options = ["receipt", "other", "corrupted"]
    doc_type = st.radio(
        "Type",
        doc_type_options,
        index=doc_type_options.index(default_doc_type),
        horizontal=True,
        key=f"doc_type_{selected}",
    )
    name = st.text_input("Name", value=effective_name, key=f"name_{selected}_{effective_name}")

    PLACEHOLDER_NAMES = {"Receipt", "Document", "Corrupted"}
    if name.strip() and name not in PLACEHOLDER_NAMES:
        confirmed_names = {confirmed for _, confirmed in name_pairs.values()}
        if name in confirmed_names:
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
    date_val = dt_cols[0].text_input("Date", value=default_date, key=f"date_{selected}")
    time_val = dt_cols[1].text_input("Time", value=default_time, key=f"time_{selected}")

    if doc_type == "receipt":
        cost_cols = st.columns([2, 1, 1])
        cost_display = str(int(default_cost)) if default_cost == int(default_cost) else str(default_cost)
        cost_str = cost_cols[0].text_input("Cost", value=cost_display, key=f"cost_{selected}")
        currency_val = cost_cols[1].text_input("Currency", value=default_currency, key=f"currency_{selected}")
        jpy_checked = cost_cols[2].checkbox("JPY", value=(default_currency.upper() == "JPY"), key=f"jpy_{selected}")
        st.markdown(
            "<style>"
            "[data-testid='stHorizontalBlock'] [data-testid='stCheckbox']"
            "{padding-top:2.1rem;}"
            "</style>",
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
            language=ext.language if isinstance(ext, ReceiptResult) else "",
            date=date_val,
            time=time_val,
            name=name,
            currency=final_currency_live,
            location=ext.location if isinstance(ext, ReceiptResult) else "",
            items=ext.items if isinstance(ext, ReceiptResult) else [],
            cost=parsed_cost_live,
        )
    elif doc_type == "other":
        live_ext = OtherResult(
            document_type="other",
            language=ext.language if isinstance(ext, OtherResult) else "",
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

    confirmed_names = {c for _, c in name_pairs.values()}
    name_previously_approved = name.strip() and name not in PLACEHOLDER_NAMES and name in confirmed_names

    parsed_cost = float(cost_str)
    final_currency = "JPY" if jpy_checked else currency_val

    def do_accept():
        if doc_type == "receipt" and (not cost_str.strip() or not final_currency):
            missing = []
            if not cost_str.strip():
                missing.append("cost")
            if not final_currency:
                missing.append("currency")
            st.error(f"Receipt requires: {', '.join(missing)}")
            return
        safe, err = is_date_time_safe_for_archive(date_val, time_val)
        if not safe:
            st.error(err)
            return
        decisions[selected] = ReviewDecision(
            verdict="accepted",
            document_type=doc_type,
            name=name,
            date=date_val,
            time=time_val,
            cost=parsed_cost,
            currency=final_currency,
        )
        save_decisions(output_path, decisions)
        st.rerun()

    if st.session_state.get("confirmed_accept") and st.session_state.get("accept_for_key") == selected:
        st.session_state.pop("confirmed_accept", None)
        st.session_state.pop("accept_for_key", None)
        do_accept()
    else:
        btn_cols = st.columns(3)
        btn_accept = btn_cols[0].button("Accept", type="primary", width='stretch', key=f"accept_{selected}")
        btn_mark = btn_cols[1].button("Mark", width='stretch', key=f"mark_{selected}")
        btn_toss = btn_cols[2].button("Toss", width='stretch', key=f"toss_{selected}")

        if btn_accept or btn_mark or btn_toss:
            if btn_accept and not name_previously_approved and name.strip() and name not in PLACEHOLDER_NAMES:
                st.session_state.accept_for_key = selected

                @st.dialog("Confirm Accept")
                def confirm_accept_dialog():
                    st.markdown("This name was not seen in previous reviews. Accept anyway?")
                    if st.button("Confirm", type="primary"):
                        st.session_state.confirmed_accept = True
                        st.rerun()

                confirm_accept_dialog()
            elif btn_accept:
                do_accept()
            else:
                verdict = "marked" if btn_mark else "tossed"
                decisions[selected] = ReviewDecision(
                    verdict=verdict,
                    document_type=doc_type,
                    name=name,
                    date=date_val,
                    time=time_val,
                    cost=parsed_cost,
                    currency=final_currency,
                )
                save_decisions(output_path, decisions)
                st.rerun()
