import json
from pathlib import Path

import streamlit as st

from data import (
    load_decisions,
    load_name_cache,
    load_name_normalizations,
    save_decisions,
    save_name_cache,
)
from models import ReviewDecision
from organize_utils import apply_reorganize
from settings import get_config

st.title("Migrate names to canonical")

cfg = get_config()
batch_dir = cfg.get("batch_output_path", "")

if not batch_dir:
    st.info("Set batch output path in Config first.")
    st.stop()

output_path = Path(batch_dir)
normalizations = load_name_normalizations(output_path)

if not normalizations:
    st.info("No name normalizations found. Add normalizations on the Normalize page first.")
    st.stop()

n_variants = len(normalizations)
st.markdown(f"**{n_variants}** variant(s) → canonical mapping(s) loaded.")

decisions = load_decisions(output_path)
name_cache = load_name_cache(output_path)


def _iter_year_month_dirs(out: Path):
    for year_dir in out.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not (month_dir.name.isdigit() or month_dir.name == "undated"):
                continue
            yield month_dir


def _iter_sidecar_paths_correct(out: Path):
    for month_dir in _iter_year_month_dirs(out):
        for p in month_dir.glob("*.json"):
            yield p
    for sub in ("marked", "tossed"):
        d = out / sub
        if d.exists():
            for p in d.glob("*.json"):
                yield p


decisions_changes = sum(1 for dec in decisions.values() if normalizations.get(dec.name, dec.name) != dec.name)
cache_changes = sum(1 for e in name_cache.values() if normalizations.get(e.get("confirmed", ""), e.get("confirmed", "")) != e.get("confirmed", ""))
sidecar_paths = list(_iter_sidecar_paths_correct(output_path))
sidecar_changes = 0
for sp in sidecar_paths:
    entry = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    review = entry.get("review", {})
    if isinstance(review, dict) and review.get("name") is not None:
        if normalizations.get(review["name"], review["name"]) != review["name"]:
            sidecar_changes += 1

st.subheader("Phase 1 — Preview JSON changes")
st.markdown(f"- **decisions.json:** {decisions_changes} name(s) would be updated.")
st.markdown(f"- **name_cache.json:** {cache_changes} confirmed name(s) would be updated (extracted left as-is).")
st.markdown(f"- **Sidecars:** {sidecar_changes} sidecar(s) would have `review.name` updated.")

if normalizations:
    sample = list(normalizations.items())[:10]
    st.dataframe([{"variant": v, "canonical": c} for v, c in sample], hide_index=True, width="stretch")

st.subheader("Phase 2 — Apply JSON migration")
if st.button("Apply JSON migration"):
    for fn, dec in decisions.items():
        new_name = normalizations.get(dec.name, dec.name)
        if new_name != dec.name:
            decisions[fn] = ReviewDecision(
                verdict=dec.verdict,
                document_type=dec.document_type,
                name=new_name,
                date=dec.date,
                time=dec.time,
                cost=dec.cost,
                currency=dec.currency,
            )
    save_decisions(output_path, decisions)

    for fn, entry in name_cache.items():
        conf = entry.get("confirmed", "")
        new_conf = normalizations.get(conf, conf)
        if new_conf != conf:
            name_cache[fn] = {**entry, "confirmed": new_conf}
    save_name_cache(output_path, name_cache)

    for sp in sidecar_paths:
        if not sp.exists():
            continue
        entry = json.loads(sp.read_text(encoding="utf-8"))
        review = entry.get("review", {})
        if isinstance(review, dict) and review.get("name") is not None:
            new_name = normalizations.get(review["name"], review["name"])
            if new_name != review["name"]:
                entry["review"] = {**review, "name": new_name}
                sp.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")

    st.success("JSON migration applied.")
    st.rerun()

st.subheader("Phase 3 — Preview file renames")
moves_preview = apply_reorganize(output_path, dry_run=True)
if not moves_preview:
    st.markdown("No file renames needed (paths already match canonical names).")
else:
    st.markdown(f"**{len(moves_preview)}** file(s) would be renamed:")
    st.dataframe(
        [{"original_filename": fn, "from": old_p, "to": new_p} for fn, old_p, new_p in moves_preview],
        hide_index=True,
        width="stretch",
    )

st.subheader("Phase 4 — Run file renames")
if moves_preview and st.button("Run file renames", type="primary"):
    apply_reorganize(output_path)
    st.success(f"Renamed {len(moves_preview)} file(s).")
    st.rerun()
