import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from data import atomic_write_text
from settings import get_config


def brand_directory_path() -> Path | None:
    batch = (get_config().batch_output_path or "").strip()
    if not batch:
        return None
    return Path(batch).expanduser().resolve() / "brand_directory.json"


class BrandEntry(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    prefixes: list[str] = Field(min_length=1)


class BrandDirectory(BaseModel):
    brands: list[BrandEntry] = Field(default_factory=list)


class ResolvedBrand(BaseModel):
    brand_id: str | None = None
    brand_label: str | None = None
    matched_prefix: str | None = None
    brand_location: str = ""


class BrandBranch(BaseModel):
    """What a prefix left over: the branch part of a name, and how many receipts carry it."""

    location: str
    receipts: int


class BrandPrefixBreakdown(BaseModel):
    """One of a brand's prefixes and the branches it actually matched."""

    prefix: str
    receipts: int
    branches: list[BrandBranch]


class PrefixSuggestion(BaseModel):
    prefix: str
    count: int
    #: The unmatched names this prefix covers - what you read to decide whether it is one brand or
    #: two shops that happen to start alike. Empty when the row is a name count, not a suggestion.
    names: list[str] = Field(default_factory=list)


def make_brand_id(label: str, existing_ids: set[str]) -> str:
    base_id = _slugify_brand_label(label)
    if base_id not in existing_ids:
        return base_id
    suffix = 2
    while f"{base_id}-{suffix}" in existing_ids:
        suffix += 1
    return f"{base_id}-{suffix}"


def _slugify_brand_label(label: str) -> str:
    normalized = unicodedata.normalize("NFKD", label)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    lower_text = ascii_text.casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", lower_text).strip("-")
    return slug or "brand"


def load_brand_directory() -> BrandDirectory:
    path = brand_directory_path()
    if path is None or not path.exists():
        return BrandDirectory()
    data = json.loads(path.read_text(encoding="utf-8"))
    return BrandDirectory.model_validate({**BrandDirectory().model_dump(), **data})


def save_brand_directory(directory: BrandDirectory) -> None:
    path = brand_directory_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic, like every other store: a crash or a second writer must not truncate the registry.
    atomic_write_text(path, json.dumps(directory.model_dump(), indent=2, ensure_ascii=False))


def brand_registry_mtime() -> float:
    path = brand_directory_path()
    return path.stat().st_mtime if path is not None and path.exists() else 0.0


def resolve_brand(merchant_name: str, directory: BrandDirectory) -> ResolvedBrand:
    if not merchant_name or not directory.brands:
        return ResolvedBrand()
    name_cf = merchant_name.casefold()
    candidates: list[tuple[int, int, int, BrandEntry, str]] = []
    for bi, brand in enumerate(directory.brands):
        for pi, prefix in enumerate(brand.prefixes):
            p = prefix.strip()
            if not p:
                continue
            if name_cf.startswith(p.casefold()):
                candidates.append((len(p), bi, pi, brand, p))
    if not candidates:
        return ResolvedBrand()
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    _, _, _, best_brand, best_prefix = candidates[0]
    remainder = merchant_name[len(best_prefix) :].strip()
    return ResolvedBrand(
        brand_id=best_brand.id,
        brand_label=best_brand.label,
        matched_prefix=best_prefix,
        brand_location=remainder,
    )


def brand_breakdown(
    records: pd.DataFrame, directory: BrandDirectory
) -> dict[str, list[BrandPrefixBreakdown]]:
    """For every brand: each of its prefixes, and the branch names that prefix resolved to.

    This is what the Manage page draws as a tree. A prefix that matched nothing comes back with no
    branches and no receipts rather than being left out - a prefix that does nothing is exactly what
    you want to see when you are deciding whether to keep it.

    Resolution runs once per DISTINCT name (a few thousand) instead of once per receipt, because
    ``resolve_brand`` only ever looks at the name.
    """
    matched: dict[str, dict[str, dict[str, int]]] = {}
    if not records.empty and "document_type" in records.columns and "name" in records.columns:
        receipts = records[records["document_type"] == "receipt"]
        names = receipts["name"].fillna("").astype(str).str.strip()
        for name, count in names[names != ""].value_counts().items():
            resolved = resolve_brand(str(name), directory)
            if not resolved.brand_id or resolved.matched_prefix is None:
                continue
            by_prefix = matched.setdefault(resolved.brand_id, {})
            branches = by_prefix.setdefault(resolved.matched_prefix, {})
            branches[resolved.brand_location] = branches.get(resolved.brand_location, 0) + int(count)

    out: dict[str, list[BrandPrefixBreakdown]] = {}
    for brand in directory.brands:
        by_prefix = matched.get(brand.id, {})
        nodes = []
        for prefix in brand.prefixes:
            branches = by_prefix.get(prefix.strip(), {})
            ranked = sorted(branches.items(), key=lambda pair: (-pair[1], pair[0]))
            nodes.append(BrandPrefixBreakdown(
                prefix=prefix,
                receipts=sum(branches.values()),
                branches=[BrandBranch(location=location, receipts=count) for location, count in ranked],
            ))
        out[brand.id] = nodes
    return out


def enrich_receipt_brand_columns(
    df: pd.DataFrame, directory: BrandDirectory
) -> pd.DataFrame:
    if df.empty or "document_type" not in df.columns:
        return df
    brand_ids: list[str | None] = []
    brand_labels: list[str | None] = []
    brand_locs: list[str] = []
    groups: list[str] = []
    for _, row in df.iterrows():
        if row.get("document_type") != "receipt":
            brand_ids.append(None)
            brand_labels.append(None)
            brand_locs.append("")
            groups.append("")
            continue
        name = str(row.get("name") or "")
        rb = resolve_brand(name, directory)
        brand_ids.append(rb.brand_id)
        brand_labels.append(rb.brand_label)
        brand_locs.append(rb.brand_location)
        if rb.brand_id and rb.brand_label:
            groups.append(rb.brand_label)
        else:
            groups.append(name)
    out = df.copy()
    out["brand_id"] = brand_ids
    out["brand_label"] = brand_labels
    out["brand_location"] = brand_locs
    out["merchant_group"] = groups
    return out


def build_prefix_suggestions(
    unmatched_names: list[str],
    *,
    boundary_only: bool = True,
    max_length: int = 24,
    min_length: int = 3,
    min_count: int = 2,
) -> list[PrefixSuggestion]:
    # Matching is casefolded, but the page shows the name as it is spelled on the receipts, so keep
    # the first spelling of each folded name rather than the folded form.
    spelled: dict[str, str] = {}
    for name in unmatched_names:
        text = str(name).strip()
        if text:
            spelled.setdefault(text.casefold(), text)
    if not spelled:
        return []
    upper = max(max_length, 1)
    lower = max(min_length, 1)
    counts: dict[str, int] = {}
    members: dict[str, list[str]] = {}
    for name, original in spelled.items():
        for prefix in _candidate_prefixes(name, boundary_only=boundary_only, max_length=upper, min_length=lower):
            counts[prefix] = counts.get(prefix, 0) + 1
            members.setdefault(prefix, []).append(original)
    kept = [prefix for prefix, count in counts.items() if len(prefix) >= lower and count >= min_count]
    suppressed: set[str] = set()
    for short_prefix in kept:
        short_count = counts[short_prefix]
        for long_prefix in kept:
            if len(long_prefix) <= len(short_prefix):
                continue
            if counts[long_prefix] != short_count:
                continue
            if long_prefix.startswith(short_prefix):
                suppressed.add(short_prefix)
                break
    ranked = [prefix for prefix in kept if prefix not in suppressed]
    ranked.sort(key=lambda prefix: (-counts[prefix], -len(prefix), prefix))
    return [PrefixSuggestion(prefix=prefix, count=counts[prefix], names=members[prefix])
            for prefix in ranked]


def _candidate_prefixes(
    name: str,
    *,
    boundary_only: bool,
    max_length: int,
    min_length: int,
) -> list[str]:
    limit = min(len(name), max_length)
    if limit < min_length:
        return []
    prefixes: list[str] = []
    if boundary_only:
        for i in range(1, limit + 1):
            if i < len(name) and name[i].isalnum():
                continue
            candidate = name[:i].strip()
            if len(candidate) >= min_length:
                prefixes.append(candidate)
        return list(dict.fromkeys(prefixes))
    for i in range(min_length, limit + 1):
        candidate = name[:i].rstrip()
        if len(candidate) >= min_length:
            prefixes.append(candidate)
    # Deduped like the boundary branch: two cut points can land on the same prefix once trailing
    # space is stripped ("tealive pj"[:7] and [:8]), and a name must only count once towards it.
    return list(dict.fromkeys(prefixes))

def brand_overview(records: pd.DataFrame, directory: BrandDirectory) -> tuple[int, int, list[PrefixSuggestion]]:
    """(receipts, matched receipts, unmatched names with counts) for the Brand registry page.

    Receipts whose name matches no brand prefix are what the registry is for: they are what the
    suggestions are built from, and what the page lists so you can see what is still ungrouped.
    """
    if records.empty or "document_type" not in records.columns:
        return 0, 0, []
    receipts = records[records["document_type"] == "receipt"]
    if receipts.empty:
        return 0, 0, []
    if "brand_id" in receipts.columns:
        unmatched = receipts[receipts["brand_id"].isna() | (receipts["brand_id"] == "")]
    else:
        unmatched = receipts
    # Receipts with no name at all are still ungrouped, so they are listed rather than silently
    # inflating the count the page shows above the table.
    names = unmatched["name"].fillna("").astype(str).str.strip().replace("", "(no name)")
    counts = names.value_counts()
    rows = [PrefixSuggestion(prefix=str(name), count=int(count)) for name, count in counts.items()]
    return int(len(receipts)), int(len(receipts) - len(unmatched)), rows
