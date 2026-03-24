import json
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from settings import CONFIG_PATH

BRAND_DIRECTORY_PATH = CONFIG_PATH.with_name("brand_directory.json")


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


def load_brand_directory() -> BrandDirectory:
    if not BRAND_DIRECTORY_PATH.exists():
        return BrandDirectory()
    data = json.loads(BRAND_DIRECTORY_PATH.read_text(encoding="utf-8"))
    return BrandDirectory.model_validate({**BrandDirectory().model_dump(), **data})


def save_brand_directory(directory: BrandDirectory) -> None:
    BRAND_DIRECTORY_PATH.write_text(
        json.dumps(directory.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def brand_registry_mtime() -> float:
    return BRAND_DIRECTORY_PATH.stat().st_mtime if BRAND_DIRECTORY_PATH.exists() else 0.0


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
