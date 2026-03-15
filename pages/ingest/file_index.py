from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image

from data import (
    build_document_index,
    clear_extractions_decisions_for_batch,
    load_decisions,
    load_document_groups,
    replace_groups_for_batch,
    save_decisions,
)
from indexing_schemes import SCHEMES, parse_canon_filename
from models import (
    DocumentKey,
    ReviewDecision,
    ScanBatch,
    ScanIndex,
    batch_serial_key,
    filename_to_batch_serial,
    load_scan_index,
)
from settings import IMAGE_EXTENSIONS, get_config

st.title("File Index")

cfg = get_config()
input_dir = cfg.get("input_image_path", "")
output_dir = cfg.get("batch_output_path", "")

if not input_dir or not output_dir:
    st.info("Set input image path and batch output path in Config first.")
    st.stop()

input_path = Path(input_dir)
output_path = Path(output_dir)

image_files = [f for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
all_filenames = [f.name for f in image_files]

existing_index: ScanIndex | None = None
indexed_filenames: set[str] = set()
index_file = output_path / "batches.json"
if index_file.exists():
    existing_index = load_scan_index(output_path)
    indexed_filenames = set(filename_to_batch_serial(existing_index).keys())

unindexed_filenames = [fn for fn in all_filenames if fn not in indexed_filenames]

st.subheader("Summary")
col0, col1, col2 = st.columns(3)
col0.metric("Image files", len(image_files))
col1.metric("Indexed", len(indexed_filenames))
col2.metric("Unindexed", len(unindexed_filenames))

if existing_index:
    st.markdown(f"**Existing index:** {len(existing_index.batches)} batch(es)")

has_batches = existing_index and len(existing_index.batches) > 0
if not unindexed_filenames and not has_batches:
    st.success("All files are already indexed.")
    st.stop()

if unindexed_filenames:
    scheme_name = st.selectbox(
        "Indexing scheme",
        options=list(SCHEMES.keys()),
        help="Only unindexed files are passed to the scheme. Already-indexed files are never reassigned.",
    )
    scheme = SCHEMES[scheme_name]
    new_batches_rel, scheme_skipped, warnings = scheme(unindexed_filenames)

    has_error = False
    if scheme_name == "Canon ImageFormula" and existing_index and existing_index.batches and new_batches_rel:
        last_batch = existing_index.batches[-1]
        last_end = datetime.strptime(last_batch.end_datetime, "%Y-%m-%d %H:%M:%S")
        unindexed_parsed = [(parse_canon_filename(fn), fn) for fn in unindexed_filenames if fn not in scheme_skipped]
        before_last = [fn for (p, fn) in unindexed_parsed if p and p[0] < last_end]
        if before_last:
            has_error = True
            st.error(
                f"{len(before_last)} unindexed file(s) have timestamps before the last batch's end "
                f"({last_batch.end_datetime}). This indicates files were missed. "
                f"Delete batches.json and rebuild from scratch to fix."
            )
            with st.expander("Offending files"):
                for fn in before_last:
                    st.text(f"  {fn}")

    if scheme_skipped:
        with st.expander(f"{len(scheme_skipped)} skipped by scheme (not assigned)"):
            for name in scheme_skipped:
                st.text(name)

    if warnings:
        for w in warnings:
            st.warning(w)

    if has_error:
        st.stop()

    start_batch_id = existing_index.batches[-1].batch_id + 1 if existing_index and existing_index.batches else 1
    new_batches: list[ScanBatch] = []
    for i, b in enumerate(new_batches_rel):
        new_batches.append(ScanBatch(
            batch_id=start_batch_id + i,
            start_datetime=b.start_datetime,
            end_datetime=b.end_datetime,
            files=b.files,
        ))

    if new_batches:
        st.subheader("Confirm batches")
        for b in new_batches:
            with st.expander(f"Batch {b.batch_id} — {len(b.files)} files — {b.start_datetime} to {b.end_datetime}"):
                for serial in sorted(b.files):
                    st.text(f"  {serial:03d}  {b.files[serial]}")

        if st.button("Confirm Batches", width="stretch", type="primary"):
            output_path.mkdir(parents=True, exist_ok=True)
            final_batches = (existing_index.batches + new_batches) if existing_index else new_batches
            index = ScanIndex(batches=final_batches)
            (output_path / "batches.json").write_text(index.model_dump_json(indent=2), encoding="utf-8")
            st.success(f"Added {len(new_batches)} batch(es). Configure document grouping below.")
            st.rerun()

if "doc_grouping_keys_by_batch" not in st.session_state:
    st.session_state.doc_grouping_keys_by_batch = {}
if "doc_grouping_links_by_batch" not in st.session_state:
    st.session_state.doc_grouping_links_by_batch = {}


def _compute_groups(k: list[str], lnk: list[bool]) -> list[list[str]]:
    if not k:
        return []
    groups: list[list[str]] = []
    current = [k[0]]
    for i in range(1, len(k)):
        if i - 1 < len(lnk) and lnk[i - 1]:
            current.append(k[i])
        else:
            groups.append(current)
            current = [k[i]]
    groups.append(current)
    return groups


def _links_from_groups(keys: list[str], groups: list[list[str]]) -> list[bool]:
    key_to_gi: dict[str, int] = {}
    for gi, g in enumerate(groups):
        for k in g:
            key_to_gi[k] = gi
    return [key_to_gi.get(keys[i], -1) == key_to_gi.get(keys[i + 1], -2) for i in range(len(keys) - 1)]


def _group_containing(idx: int, keys: list[str], links: list[bool]) -> tuple[int, list[int]]:
    grps = _compute_groups(keys, links)
    for gi, g in enumerate(grps):
        for i, k in enumerate(g):
            if keys.index(k) == idx:
                indices = [keys.index(kk) for kk in g]
                return gi, indices
    return -1, []


def _batch_id_from_key(key: str) -> int | None:
    dk = DocumentKey.parse(key)
    return dk.batch_id if dk else None


def _build_display_keys(filtered_groups: list[list[str]], batch_keys: list[str], tossed_set: set[str]) -> list[str]:
    if not filtered_groups:
        return batch_keys
    keys_in_groups = {k for g in filtered_groups for k in g}
    batch_idx = {k: i for i, k in enumerate(batch_keys)}
    groups_by_scan = sorted(filtered_groups, key=lambda g: min(batch_idx[k] for k in g if k not in tossed_set))
    active_ordered = [k for g in groups_by_scan for k in g if k not in tossed_set]
    active_iter = iter(active_ordered)
    result = []
    for k in batch_keys:
        if k in tossed_set:
            result.append(k)
        elif k in keys_in_groups:
            result.append(next(active_iter, k))
        else:
            result.append(k)
    return result


def _split_groups_at_tossed_boundaries(groups: list[list[str]], batch_keys: list[str], tossed_set: set[str]) -> list[list[str]]:
    batch_idx = {k: i for i, k in enumerate(batch_keys)}
    result = []
    for g in groups:
        active = [k for k in g if k not in tossed_set]
        if not active:
            continue
        current = [active[0]]
        for i in range(1, len(active)):
            idx_prev = batch_idx[active[i - 1]]
            idx_curr = batch_idx[active[i]]
            lo, hi = min(idx_prev, idx_curr), max(idx_prev, idx_curr)
            if any(batch_keys[j] in tossed_set for j in range(lo + 1, hi)):
                result.append(current)
                current = [active[i]]
            else:
                current.append(active[i])
        result.append(current)
    return result


def build_display_state(
    batch_keys: list[str],
    batch_groups: list[list[str]],
    tossed_set: set[str],
) -> tuple[list[str], list[str], list[bool]]:
    filtered = _split_groups_at_tossed_boundaries(batch_groups, batch_keys, tossed_set)
    display_keys = _build_display_keys(filtered, batch_keys, tossed_set)
    active_keys = [k for k in display_keys if k not in tossed_set]
    active_links = _links_from_groups(active_keys, filtered) if filtered else [False] * max(0, len(active_keys) - 1)
    return display_keys, active_keys, active_links


ROTATION_MAP = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}


def _apply_orientation(img: Image.Image, orientation: str) -> Image.Image:
    if orientation == "←":
        return img.transpose(ROTATION_MAP[270])
    if orientation == "→":
        return img.transpose(ROTATION_MAP[90])
    if orientation == "↓":
        return img.transpose(ROTATION_MAP[180])
    return img


def _render_pagination(page: int, n_pages: int, page_key: str, batch_id: int, key_suffix: str = ""):
    suffix = f"_{key_suffix}" if key_suffix else ""
    pag_cols = st.columns([1, 1, 1, 1, 1, 1])
    with pag_cols[0]:
        if st.button("← Prev", key=f"pag_prev_{batch_id}{suffix}", disabled=(page == 0), width="stretch"):
            st.session_state[page_key] = page - 1
            st.rerun()
    with pag_cols[2]:
        st.markdown(f"<div style='text-align:center;padding-top:0.5rem;'>Page {page + 1} of {n_pages}</div>", unsafe_allow_html=True)
    with pag_cols[3]:
        page_choice = st.number_input(
            "Skip to page", min_value=1, max_value=n_pages, value=page + 1,
            key=f"pag_go_{batch_id}{suffix}_p{page}", label_visibility="collapsed",
        )
        if page_choice != page + 1:
            st.session_state[page_key] = page_choice - 1
            st.rerun()
    with pag_cols[5]:
        if st.button("Next →", key=f"pag_next_{batch_id}{suffix}", disabled=(page >= n_pages - 1), width="stretch"):
            st.session_state[page_key] = page + 1
            st.rerun()


non_archived = [b for b in existing_index.batches if not b.archived] if existing_index else []

if non_archived:
    st.subheader("Document grouping")
    st.caption("Link adjacent pages to group them into one document. Use ⇄ to swap order within a group.")

    batch_options = [(b.batch_id, f"Batch {b.batch_id} — {len(b.files)} files — {b.start_datetime} to {b.end_datetime}") for b in non_archived]
    selected_batch_id = st.selectbox("Batch", options=[opt[0] for opt in batch_options], format_func=lambda x: next(label for bid, label in batch_options if bid == x))

    selected_batch = next(b for b in non_archived if b.batch_id == selected_batch_id)
    batch_keys = [batch_serial_key(selected_batch.batch_id, ser) for ser, _ in sorted(selected_batch.files.items())]
    batch_keys_sig = frozenset(batch_keys)

    decisions = load_decisions(output_path)
    index = build_document_index(output_path, batch_keys_sig)
    tossed_set = {
        k for k in batch_keys
        if (dk := index.key_to_doc_key(k)) and decisions.get(str(dk)) and decisions[str(dk)].verdict == "tossed"
    }

    existing_doc = load_document_groups(output_path)
    batch_groups = [g for g in existing_doc.groups if g and _batch_id_from_key(g[0]) == selected_batch_id and all(k in batch_keys_sig for k in g)]
    display_keys, _, active_links_default = build_display_state(batch_keys, batch_groups, tossed_set)
    prev_keys = st.session_state.doc_grouping_keys_by_batch.get(selected_batch_id)
    prev_links = st.session_state.doc_grouping_links_by_batch.get(selected_batch_id)
    need_reinit = (
        prev_keys is None
        or set(prev_keys) != batch_keys_sig
        or prev_links is None
        or len(prev_links) != len(active_links_default)
    )
    if need_reinit:
        st.session_state.doc_grouping_keys_by_batch[selected_batch_id] = display_keys
        st.session_state.doc_grouping_links_by_batch[selected_batch_id] = active_links_default

    keys = st.session_state.doc_grouping_keys_by_batch[selected_batch_id]
    active_links = st.session_state.doc_grouping_links_by_batch[selected_batch_id]
    active_keys = [k for k in keys if k not in tossed_set]

    key_to_item = {}
    for ser, fn in selected_batch.files.items():
        key_to_item[batch_serial_key(selected_batch.batch_id, ser)] = (selected_batch.batch_id, ser, fn)

    rerun = False
    COLS = [3, 1, 3, 1, 3, 1, 3, 1, 3, 1, 3, 1]
    ROWS_PER_PAGE = 6
    total_rows = (len(keys) + 5) // 6
    n_pages = max(1, (total_rows + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    page_key = f"file_index_page_{selected_batch_id}"
    page = min(st.session_state.get(page_key, 0), max(0, n_pages - 1))
    st.session_state[page_key] = page
    _render_pagination(page, n_pages, page_key, selected_batch_id, "top")
    row_start_page = page * ROWS_PER_PAGE * 6
    for row_idx in range(ROWS_PER_PAGE):
        row_start = row_start_page + row_idx * 6
        if row_start >= len(keys):
            break
        cols = st.columns(COLS)
        for j, i in enumerate(range(row_start, min(row_start + 6, len(keys)))):
            key = keys[i]
            bid, ser, fn = key_to_item[key]
            img_path = input_path / fn
            with cols[2 * j]:
                if img_path.exists():
                    try:
                        img = Image.open(str(img_path)).convert("RGB")
                        img_btn_cols = st.columns(4)
                        for oi, orient in enumerate(["←", "→", "↓"]):
                            if img_btn_cols[oi].button(orient, key=f"dir_{selected_batch_id}_{key}_{orient}"):
                                corrected = _apply_orientation(img, orient)
                                corrected.save(str(img_path))
                                rerun = True

                        is_tossed = key in tossed_set
                        if is_tossed:
                            if img_btn_cols[3].button("↩", key=f"recover_{selected_batch_id}_{key}"):
                                dk = index.key_to_doc_key(key)
                                if dk and str(dk) in decisions:
                                    del decisions[str(dk)]
                                    save_decisions(output_path, decisions)
                                st.session_state.doc_grouping_keys_by_batch.pop(selected_batch_id, None)
                                st.session_state.doc_grouping_links_by_batch.pop(selected_batch_id, None)
                                rerun = True
                        else:
                            if img_btn_cols[3].button("X", key=f"toss_{selected_batch_id}_{key}"):
                                dk = index.key_to_doc_key(key)
                                if dk:
                                    decisions[str(dk)] = ReviewDecision(verdict="tossed", document_type="corrupted", name="", date="", time="", cost=0.0, currency="")
                                    save_decisions(output_path, decisions)
                                st.session_state.doc_grouping_keys_by_batch.pop(selected_batch_id, None)
                                st.session_state.doc_grouping_links_by_batch.pop(selected_batch_id, None)
                                rerun = True

                        st.image(img, caption=f"{key} {fn}", width="stretch")
                    except Exception:
                        st.caption(f"{key} {fn}")
                else:
                    st.caption(f"{key} {fn}")
            with cols[2 * j + 1]:
                if i < len(keys) - 1:
                    ki, ki1 = keys[i], keys[i + 1]
                    both_active = ki not in tossed_set and ki1 not in tossed_set
                    adjacent = keys.index(ki1) == keys.index(ki) + 1
                    active_link_idx = active_keys.index(ki) if both_active and ki in active_keys else None
                    is_active_toggle = (
                        both_active
                        and adjacent
                        and active_link_idx is not None
                        and active_link_idx < len(active_links)
                    )
                    if is_active_toggle:
                        new_val = st.toggle("🔗", value=active_links[active_link_idx], key=f"link_{selected_batch_id}_{i}")
                        if new_val != active_links[active_link_idx]:
                            active_links[active_link_idx] = new_val
                            rerun = True
                        if active_links[active_link_idx] and st.button("⇄", key=f"swap_{selected_batch_id}_{i}"):
                            keys[i], keys[i + 1] = keys[i + 1], keys[i]
                            rerun = True
                    else:
                        st.toggle("🔗", value=False, disabled=True, key=f"link_{selected_batch_id}_{i}")
    _render_pagination(page, n_pages, page_key, selected_batch_id, "bot")
    if rerun:
        st.rerun()

    groups_preview = _compute_groups(active_keys, active_links)
    new_multi = [g for g in groups_preview if len(g) > 1]
    has_changes = new_multi != batch_groups
    if groups_preview:
        doc_keys = [str(DocumentKey.from_group(g)) for g in groups_preview]
        multi = [dk for dk in doc_keys if "-" in dk]
        single = [dk for dk in doc_keys if "-" not in dk]
        parts = []
        if single:
            parts.append(f"{len(single)} single-page")
        if multi:
            parts.append(f"{len(multi)} multi-page: {', '.join(multi)}")
        st.caption(f"Documents: {'; '.join(parts)}")

    if st.button("Save Document Groups", width="stretch", type="primary", disabled=not has_changes):
        new_groups = _compute_groups(active_keys, active_links)
        replace_groups_for_batch(output_path, selected_batch_id, new_groups)
        clear_extractions_decisions_for_batch(output_path, selected_batch_id)
        st.success("Saved. Re-run Parse for this batch if needed.")
        st.rerun()
