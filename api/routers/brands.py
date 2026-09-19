"""The brand registry: which merchant names belong to the same brand, and where the branch name starts.

A brand is a label plus prefixes; a receipt name matches by longest prefix and the remainder becomes
its location (``brand_registry.resolve_brand``). Every write replaces ``brand_directory.json``, so the
edits are field-level, applied to the file as it is, rather than a whole registry the browser sends back.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

import brand_registry as registry
from api import cache
from api.guards import one_edit_at_a_time
from api.deps import get_output_path
from api.schemas import (
    BrandBranchOut, BrandIn, BrandOut, BrandOverview, BrandPrefixIn, BrandPrefixOut, BrandsOut, NameCount,
    PrefixSuggestionOut,
)
from settings import get_config, update_config

router = APIRouter(prefix="/api/brands", tags=["brands"])


def _directory() -> registry.BrandDirectory:
    return registry.load_brand_directory()


def _out(entry: registry.BrandEntry, trees: dict[str, list[registry.BrandPrefixBreakdown]]) -> BrandOut:
    tree = trees.get(entry.id, [])
    return BrandOut(
        id=entry.id, label=entry.label, prefixes=list(entry.prefixes),
        receipt_count=sum(node.receipts for node in tree),
        tree=[BrandPrefixOut(prefix=node.prefix, receipts=node.receipts,
                             branches=[BrandBranchOut(location=b.location, receipts=b.receipts)
                                       for b in node.branches])
              for node in tree],
    )


def _trees(output_path: Path, directory: registry.BrandDirectory
           ) -> dict[str, list[registry.BrandPrefixBreakdown]]:
    """Which branches each prefix matched — the tree the Manage section draws, and the receipt counts."""
    return registry.brand_breakdown(cache.viz_records(output_path), directory)


def _save(directory: registry.BrandDirectory) -> None:
    registry.save_brand_directory(directory)
    cache.clear()  # every viz page groups by brand


def _find(directory: registry.BrandDirectory, brand_id: str) -> registry.BrandEntry:
    entry = next((b for b in directory.brands if b.id == brand_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no brand {brand_id}")
    return entry


def _clean_prefixes(prefixes: list[str]) -> list[str]:
    cleaned = list(dict.fromkeys(p.strip() for p in prefixes if p.strip()))
    if not cleaned:
        raise HTTPException(status_code=422, detail="a brand needs at least one prefix")
    return cleaned


@router.get("", response_model=BrandsOut)
def list_brands(output_path: Path = Depends(get_output_path)):
    """Every brand with how many archived receipts it matches, plus the unmatched ones."""
    directory = _directory()
    trees = _trees(output_path, directory)
    receipts, matched, unmatched = registry.brand_overview(cache.viz_records(output_path), directory)
    return BrandsOut(
        brands=[_out(entry, trees) for entry in directory.brands],
        overview=BrandOverview(receipts=receipts, matched=matched, unmatched=receipts - matched),
        unmatched=[NameCount(name=row.prefix, count=row.count) for row in unmatched],
    )


@router.post("", response_model=BrandOut, status_code=201)
@one_edit_at_a_time
def create_brand(body: BrandIn, output_path: Path = Depends(get_output_path)):
    directory = _directory()
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="a brand needs a label")
    entry = registry.BrandEntry(id=registry.make_brand_id(label, {b.id for b in directory.brands}),
                                label=label, prefixes=_clean_prefixes(body.prefixes))
    saved = registry.BrandDirectory(brands=[*directory.brands, entry])
    _save(saved)
    return _out(entry, _trees(output_path, saved))


@router.patch("/{brand_id}", response_model=BrandOut)
@one_edit_at_a_time
def update_brand(brand_id: str, body: BrandIn, output_path: Path = Depends(get_output_path)):
    """Rename a brand or replace its prefixes. The id never changes, so receipts keep their grouping."""
    directory = _directory()
    entry = _find(directory, brand_id)
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="a brand needs a label")
    updated = entry.model_copy(update={"label": label, "prefixes": _clean_prefixes(body.prefixes)})
    saved = registry.BrandDirectory(brands=[updated if b.id == brand_id else b for b in directory.brands])
    _save(saved)
    return _out(updated, _trees(output_path, saved))


@router.post("/{brand_id}/prefixes", response_model=BrandOut)
@one_edit_at_a_time
def add_prefix(brand_id: str, body: BrandPrefixIn, output_path: Path = Depends(get_output_path)):
    """Add one prefix — the quick action on a suggestion."""
    directory = _directory()
    entry = _find(directory, brand_id)
    updated = entry.model_copy(update={"prefixes": _clean_prefixes([*entry.prefixes, body.prefix])})
    saved = registry.BrandDirectory(brands=[updated if b.id == brand_id else b for b in directory.brands])
    _save(saved)
    return _out(updated, _trees(output_path, saved))


@router.delete("/{brand_id}", response_model=BrandOverview)
@one_edit_at_a_time
def delete_brand(brand_id: str, output_path: Path = Depends(get_output_path)):
    """Remove a brand. Its receipts stay where they are; they simply stop being grouped."""
    directory = _directory()
    _find(directory, brand_id)
    _save(registry.BrandDirectory(brands=[b for b in directory.brands if b.id != brand_id]))
    receipts, matched, _unmatched = registry.brand_overview(cache.viz_records(output_path), _directory())
    return BrandOverview(receipts=receipts, matched=matched, unmatched=receipts - matched)


@router.get("/suggestions", response_model=list[PrefixSuggestionOut])
def suggestions(
    boundary_only: bool | None = None,
    max_length: int | None = Query(None, ge=4, le=80),
    min_length: int | None = Query(None, ge=1, le=24),
    min_count: int | None = Query(None, ge=1, le=50),
    output_path: Path = Depends(get_output_path),
):
    """Prefixes shared by unmatched receipt names. The settings are remembered for the next visit."""
    cfg = get_config()
    settings = dict(
        boundary_only=cfg.prefix_suggestion_boundary_only if boundary_only is None else boundary_only,
        max_length=cfg.prefix_suggestion_max_length if max_length is None else max_length,
        min_length=cfg.prefix_suggestion_min_length if min_length is None else min_length,
        min_count=cfg.prefix_suggestion_min_count if min_count is None else min_count,
    )
    update_config(prefix_suggestion_boundary_only=settings["boundary_only"],
                  prefix_suggestion_max_length=settings["max_length"],
                  prefix_suggestion_min_length=settings["min_length"],
                  prefix_suggestion_min_count=settings["min_count"])
    _receipts, _matched, unmatched = registry.brand_overview(cache.viz_records(output_path), _directory())
    names = [row.prefix for row in unmatched]   # build_prefix_suggestions counts distinct names
    return [PrefixSuggestionOut(prefix=s.prefix, count=s.count, names=s.names)
            for s in registry.build_prefix_suggestions(names, **settings)]
