from pathlib import Path

import streamlit as st
from rapidfuzz.distance import Levenshtein

from data import (
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
    OtherResult,
    ReceiptResult,
    ReviewDecision,
)
from rules.cost_large_check import cost_large_check
from rules.cost_zero_check import cost_zero_check
from rules.currency_uncommon_check import currency_uncommon_check
from rules.date_check import date_check
from settings import get_config
from validation import ValidationRule

SMART_MATCH_THRESHOLD = 0.35


def get_smart_match_suggestions(
    query: str, name_pairs: dict[str, tuple[str, str]], top_n: int = 5
) -> tuple[list[str], float | None]:
    if not name_pairs or not query:
        return [], None
    scored: list[tuple[float, str]] = []
    for extracted, confirmed in name_pairs.values():
        sim = Levenshtein.normalized_similarity(query, extracted)
        if sim >= SMART_MATCH_THRESHOLD:
            scored.append((sim, confirmed))
    scored.sort(key=lambda x: -x[0])
    best_sim = scored[0][0] if scored else None
    confirmed_best_rank: dict[str, int] = {}
    confirmed_frequency: dict[str, int] = {}
    for rank, (_sim, confirmed) in enumerate(scored, 1):
        if confirmed not in confirmed_best_rank:
            confirmed_best_rank[confirmed] = rank
        confirmed_frequency[confirmed] = confirmed_frequency.get(confirmed, 0) + 1
    unique_confirmed = list(confirmed_best_rank.keys())
    unique_confirmed.sort(key=lambda c: (confirmed_best_rank[c], -confirmed_frequency[c]))
    return unique_confirmed[:top_n], best_sim


st.title("Review")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")
image_dir = cfg.get("input_image_path", "")

if not batch_dir:
    st.stop()

output_path = Path(batch_dir)

batch = load_ocr_results(output_path)
succeeded = [r for r in batch.results if r.succeeded]
all_filenames = [r.filename for r in succeeded]
ocr_by_file = {r.filename: r.markdown for r in succeeded}

extractions = {k: v for k, v in load_extractions(output_path).items() if k in set(all_filenames)}

decisions = load_decisions(output_path)

name_pairs: dict[str, tuple[str, str]] = {}
for fn, dec in decisions.items():
    if dec.verdict == "accepted" and fn in extractions:
        ext_i = extractions[fn]
        if isinstance(ext_i, ReceiptResult):
            name_pairs[fn] = (ext_i.name, dec.name)
        elif isinstance(ext_i, OtherResult):
            name_pairs[fn] = (ext_i.title, dec.name)
name_cache = load_name_cache(output_path)
for fn, entry in name_cache.items():
    name_pairs[fn] = (entry["extracted"], entry["confirmed"])

extracted_files = [f for f in all_filenames if f in extractions]
if not extracted_files:
    st.info("No extractions yet. Run Parse first.")
    st.stop()

# cost_check Disabled for now because even gpt4.1 does a bad job distinguishing tax inclusivity
VALIDATION_RULES: list[ValidationRule] = [date_check, cost_zero_check, cost_large_check, currency_uncommon_check]


def _review_sort_key(filename):
    ext = extractions[filename]
    if isinstance(ext, ReceiptResult):
        return (2, ext.name.lower())
    elif isinstance(ext, OtherResult):
        return (1, (ext.title or "").lower())
    return (0, "")


to_review = sorted(
    (f for f in extracted_files if f not in decisions),
    key=_review_sort_key,
)
total = len(extracted_files)

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
    decisions.clear()
    save_decisions(output_path, decisions)
    st.rerun()

if not to_review:
    st.success("All items reviewed!")
    st.stop()

if "review_idx" not in st.session_state or st.session_state.review_idx >= len(to_review):
    st.session_state.review_idx = 0

selected = to_review[st.session_state.review_idx]
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
    ocr_text = ocr_by_file.get(selected, "")
    st.markdown(ocr_text.replace("\n", "  \n"), unsafe_allow_html=True)

with image_col:
    if img_dir:
        img_path = img_dir / selected
        if img_path.exists():
            st.image(str(img_path), width="stretch")

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
    for rule in VALIDATION_RULES:
        for result in rule(live_ext):
            if result.color:
                st.markdown(
                    f'<span style="color:{result.color};font-weight:600;">{result.message}</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"**{result.message}**")

    btn_cols = st.columns(3)
    btn_accept = btn_cols[0].button("Accept", type="primary", width='stretch', key=f"accept_{selected}")
    btn_mark = btn_cols[1].button("Mark", width='stretch', key=f"mark_{selected}")
    btn_toss = btn_cols[2].button("Toss", width='stretch', key=f"toss_{selected}")

    if btn_accept or btn_mark or btn_toss:
        verdict = "accepted" if btn_accept else "marked" if btn_mark else "tossed"
        try:
            parsed_cost = float(cost_str)
        except ValueError:
            parsed_cost = 0.0
        final_currency = "JPY" if jpy_checked else currency_val

        if btn_accept and doc_type == "receipt" and (not parsed_cost or not final_currency):
            missing = []
            if not parsed_cost:
                missing.append("cost")
            if not final_currency:
                missing.append("currency")
            st.error(f"Receipt requires: {', '.join(missing)}")
        else:
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
