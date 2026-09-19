"""Merging merchant name variants: what it touches, and that the preview says so first."""

import pytest

import name_merge
from data import (
    load_decisions, load_distinct_pairs, load_name_normalizations, load_smart_match_cache, read_sidecar,
    save_decisions, save_name_normalizations, save_smart_match_cache,
)
from models import ReviewDecision
from viz_records import build_viz_records


def _two_names(archive_dir):
    """(target, variant) — two archived names, the variant carrying at least one document."""
    by_name, _canonical = name_merge.names_in_use(archive_dir)
    ordered = sorted(by_name, key=lambda name: (-len(by_name[name]), name))
    return ordered[0], ordered[1]


def test_plan_merge_reports_what_would_change_without_touching_anything(archive_dir):
    target, variant = _two_names(archive_dir)
    before = {p: (archive_dir / p).exists() for p in build_viz_records(archive_dir)["path"]}
    before_normalizations = load_name_normalizations(archive_dir)

    plan = name_merge.plan_merge(archive_dir, target, [variant])

    assert plan.error is None and plan.variants == [variant]
    assert plan.documents == len(name_merge.names_in_use(archive_dir)[0][variant])
    assert plan.moves and all(old != new and target in new for old, new in plan.moves)
    assert all((archive_dir / p).exists() == existed for p, existed in before.items())   # nothing moved
    assert load_name_normalizations(archive_dir) == before_normalizations


def test_apply_merge_rewrites_the_sidecars_registries_and_refiles(archive_dir):
    target, variant = _two_names(archive_dir)
    variant_files = list(name_merge.names_in_use(archive_dir)[0][variant])
    # a mid-ingest decision and a smart-match entry still carrying the old spelling
    save_decisions(archive_dir, {"9:1": ReviewDecision(verdict="accepted", document_type="receipt", name=variant,
                                                      date="2025-03-15", time="12:00", cost=1.0, currency="JPY")})
    save_smart_match_cache(archive_dir, {"9:1": {"extracted": variant, "confirmed": variant, "extracted_phone": ""}})

    result = name_merge.apply_merge(archive_dir, target, [variant])

    assert load_name_normalizations(archive_dir)[variant] == target
    assert load_decisions(archive_dir)["9:1"].name == target
    assert load_smart_match_cache(archive_dir)["9:1"]["confirmed"] == target
    names = {row["name"] for _, row in build_viz_records(archive_dir).iterrows()}
    assert variant not in names and target in names
    assert len(result.moves) == len(variant_files)
    for _old, new in result.moves:
        assert (archive_dir / new).exists() and read_sidecar(archive_dir / new).review.name == target


def test_merging_retargets_names_that_pointed_at_the_variant(archive_dir):
    target, variant = _two_names(archive_dir)
    normalizations = {**load_name_normalizations(archive_dir), "Older Spelling": variant}
    save_name_normalizations(archive_dir, normalizations)

    name_merge.apply_merge(archive_dir, target, [variant])

    after = load_name_normalizations(archive_dir)
    assert after["Older Spelling"] == target and after[variant] == target


@pytest.mark.parametrize("target, variants, message", [
    ("  ", ["a"], "Pick a name to merge into."),
    ("Target", [], "Pick at least one other name."),
    ("Target", ["Target"], "Pick at least one other name."),
])
def test_a_merge_needs_a_target_and_something_to_merge(archive_dir, target, variants, message):
    assert name_merge.plan_merge(archive_dir, target, variants).error == message
    with pytest.raises(ValueError):
        name_merge.apply_merge(archive_dir, target, variants)


def test_confirmed_different_pairs_hide_a_cluster_and_can_be_undone(archive_dir):
    members = ["A", "B", "C"]
    before = len(load_distinct_pairs(archive_dir))
    name_merge.confirm_different(archive_dir, members)
    pairs = load_distinct_pairs(archive_dir)

    assert len(pairs) == before + 3
    assert name_merge.filter_by_distinct_pairs(members, pairs) == []

    name_merge.forget_different(archive_dir, "A", "B")
    assert name_merge.filter_by_distinct_pairs(members, load_distinct_pairs(archive_dir)) == ["A", "B"]


def test_visible_groups_skip_names_no_document_uses(archive_dir):
    by_name, canonical = name_merge.names_in_use(archive_dir)
    used = sorted(by_name)[:2]
    clusters = {0: used, 1: ["Ghost One", "Ghost Two"]}

    groups = name_merge.visible_groups(clusters, by_name, canonical, set())

    assert [group.id for group in groups] == [0]
    assert groups[0].counts == {name: len(by_name[name]) for name in used}
