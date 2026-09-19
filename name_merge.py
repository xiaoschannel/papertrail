"""Merging merchant names that are the same shop written differently (the Normalize page).

A merge is the widest-reaching edit in the app: the chosen spelling replaces every variant in the
archived sidecars, in ``decisions.json``, in the smart-match cache and in ``name_normalizations.json``,
and the affected documents are then re-filed, because the file name is built from the merchant name.

Ported from ``pages/curate/normalize.py`` free of Streamlit, with a preview: :func:`plan_merge` says
exactly what a merge would touch before anything is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from data import (
    load_decisions, load_distinct_pairs, load_name_normalizations, load_reorganized_state,
    load_smart_match_cache, read_sidecar, save_decisions, save_distinct_pairs, save_name_normalizations,
    save_smart_match_cache, write_sidecar,
)
from dedupe_candidates import drop_confirmed_different as filter_by_distinct_pairs
from organize_utils import apply_reorganize, resolve_single_accepted_destination


@dataclass
class NameGroup:
    """Names one engine thinks are the same shop, and how many documents carry each."""

    id: int
    names: list[str]
    counts: dict[str, int]
    canonical: list[str]


@dataclass
class MergePlan:
    """What a merge would change. ``moves`` is (from, to) for every file that would be re-filed."""

    target: str
    variants: list[str]
    documents: int
    moves: list[tuple[str, str]] = field(default_factory=list)
    decisions: int = 0
    smart_matches: int = 0
    error: str | None = None


def names_in_use(output_path: Path) -> tuple[dict[str, list[str]], set[str]]:
    """(name -> archived filenames, names that are already a normalization target)."""
    _tossed, accepted = load_reorganized_state(output_path)
    by_name: dict[str, list[str]] = {}
    for filename, (sidecar, _rel) in accepted.items():
        if sidecar.review.name:
            by_name.setdefault(sidecar.review.name, []).append(filename)
    return by_name, set(load_name_normalizations(output_path).values())


def visible_groups(clusters: dict[int, list[str]], by_name: dict[str, list[str]],
                   canonical: set[str], pairs: set[frozenset[str]]) -> list[NameGroup]:
    """Clusters worth showing: at least one name in use, and two names not already ruled apart."""
    groups: list[NameGroup] = []
    for cluster_id, members in sorted(clusters.items()):
        if not any(member in by_name for member in members):
            continue
        kept = filter_by_distinct_pairs(members, pairs)
        if len(kept) < 2:
            continue
        groups.append(NameGroup(
            id=cluster_id, names=kept,
            counts={name: len(by_name.get(name, [])) for name in kept},
            canonical=[name for name in kept if name in canonical],
        ))
    return groups


def plan_merge(output_path: Path, target: str, variants: list[str]) -> MergePlan:
    """What merging ``variants`` into ``target`` would do — without doing any of it."""
    variants = [name for name in dict.fromkeys(variants) if name != target]
    by_name, _canonical = names_in_use(output_path)
    if not target.strip():
        return MergePlan(target=target, variants=variants, documents=0, error="Pick a name to merge into.")
    if not variants:
        return MergePlan(target=target, variants=[], documents=0, error="Pick at least one other name.")

    normalizations = _merged_normalizations(load_name_normalizations(output_path), target, variants)
    decisions = sum(1 for decision in load_decisions(output_path).values()
                    if normalizations.get(decision.name, decision.name) != decision.name)
    cache = load_smart_match_cache(output_path)
    smart = sum(1 for entry in cache.values()
                if normalizations.get(entry.get("confirmed", ""), entry.get("confirmed", "")) != entry.get("confirmed", ""))
    documents = sum(len(by_name.get(variant, [])) for variant in variants)
    return MergePlan(target=target, variants=variants, documents=documents,
                     moves=_planned_moves(output_path, target, variants, by_name),
                     decisions=decisions, smart_matches=smart)


def apply_merge(output_path: Path, target: str, variants: list[str]) -> MergePlan:
    """Do the merge: rewrite the sidecars and registries, then re-file what the new name changed."""
    plan = plan_merge(output_path, target, variants)
    if plan.error:
        raise ValueError(plan.error)

    by_name, _canonical = names_in_use(output_path)
    _tossed, accepted = load_reorganized_state(output_path)
    normalizations = _merged_normalizations(load_name_normalizations(output_path), target, plan.variants)

    for variant in plan.variants:
        for filename in by_name.get(variant, []):
            relative = accepted[filename][1]
            if not relative:          # the sidecar's image is missing; output_path/"" is the archive itself
                continue
            sidecar = read_sidecar(output_path / relative)
            if sidecar is None:
                continue
            review = sidecar.review.model_copy(update={"name": target})
            write_sidecar(output_path / relative, sidecar.model_copy(update={"review": review}))
    save_name_normalizations(output_path, normalizations)

    decisions = load_decisions(output_path)
    changed = False
    for key, decision in decisions.items():
        new_name = normalizations.get(decision.name, decision.name)
        if new_name != decision.name:
            decisions[key] = decision.model_copy(update={"name": new_name})
            changed = True
    if changed:
        save_decisions(output_path, decisions)

    cache = load_smart_match_cache(output_path)
    changed = False
    for key, entry in cache.items():
        confirmed = entry.get("confirmed", "")
        renamed = normalizations.get(confirmed, confirmed)
        if renamed != confirmed:
            cache[key] = {**entry, "confirmed": renamed}
            changed = True
    if changed:
        save_smart_match_cache(output_path, cache)

    # The name is part of the file name, so the affected documents are re-filed by the same routine
    # that fixes any other stale name (it returns (original filename, old path, new path)).
    moves = apply_reorganize(output_path)
    return MergePlan(target=target, variants=plan.variants, documents=plan.documents,
                     moves=[(old_path, new_path) for _fn, old_path, new_path in moves],
                     decisions=plan.decisions, smart_matches=plan.smart_matches)


def confirm_different(output_path: Path, names: list[str]) -> int:
    """Remember that these names are different shops, so the cluster stops coming back."""
    pairs = load_distinct_pairs(output_path)
    for first, second in combinations(dict.fromkeys(names), 2):
        pairs.add(frozenset({first, second}))
    save_distinct_pairs(output_path, pairs)
    return len(pairs)


def forget_different(output_path: Path, first: str, second: str) -> int:
    pairs = load_distinct_pairs(output_path)
    pairs.discard(frozenset({first, second}))
    save_distinct_pairs(output_path, pairs)
    return len(pairs)


def _merged_normalizations(normalizations: dict[str, str], target: str, variants: list[str]) -> dict[str, str]:
    """``variants`` (and anything already pointing at one of them) now point at ``target``."""
    merged = dict(normalizations)
    for variant in variants:
        merged[variant] = target
        for source, points_to in list(merged.items()):
            if points_to == variant:
                merged[source] = target
    merged.pop(target, None)
    return merged


def _planned_moves(output_path: Path, target: str, variants: list[str],
                   by_name: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Where the affected documents would end up once they carry ``target``.

    A preview, so it resolves each document on its own: if several of them land in the same folder on
    the same day, the real merge may add a " (2)" this doesn't show.
    """
    _tossed, accepted = load_reorganized_state(output_path)
    moves: list[tuple[str, str]] = []
    for variant in variants:
        for filename in by_name.get(variant, []):
            sidecar, relative = accepted.get(filename, (None, ""))
            if sidecar is None or not relative:
                continue
            decision = sidecar.review.model_copy(update={"name": target})
            destination = resolve_single_accepted_destination(
                output_path, sidecar.original_filename, decision, exclude_stem=Path(relative).stem)
            if destination != relative:
                moves.append((relative, destination))
    return moves
