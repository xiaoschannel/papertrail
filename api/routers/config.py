"""Config read/write — thin wrapper over settings.get_config/save_config/update_config."""

from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import ConfigDict, create_model

from api import ingest_registry as registry
from api.schemas import ConfigOptions, NormalizeEngineOut, PathCheck
from indexing_schemes import SCHEMES
from name_similarity import DEFAULT_THRESHOLD
from normalize_engines import ENGINES
from settings import AppConfig, get_config, save_config, update_config

router = APIRouter(prefix="/api/config", tags=["config"])

#: Every AppConfig field, all optional: a PATCH changes only the fields it sends.
AppConfigPatch = create_model(
    "AppConfigPatch",
    __config__=ConfigDict(extra="forbid"),
    **{name: (field.annotation | None, None) for name, field in AppConfig.model_fields.items()},
)


@router.get("")
def read_config() -> AppConfig:
    return get_config()


@router.put("")
def write_config(cfg: AppConfig) -> AppConfig:
    save_config(cfg)
    return get_config()


@router.patch("")
def patch_config(patch: AppConfigPatch) -> AppConfig:  # type: ignore[valid-type]
    """Change only the given fields, so pages editing one setting never revert another page's edits."""
    update_config(**patch.model_dump(exclude_unset=True))
    return get_config()


@router.get("/options", response_model=ConfigOptions)
def config_options():
    """The choices behind the Config page's dropdowns. Listing OCR models imports torch, so this is
    only called by that page."""
    return ConfigOptions(
        ocr_models=list(registry.ocr_providers()),
        extractors=list(registry.extractors()),
        normalize_engines=[NormalizeEngineOut(id=key, label=engine.label or key) for key, engine in ENGINES.items()],
        indexing_schemes=list(SCHEMES),
        dashboard_rank_by=["Total Spend", "Visit Count"],
        embedding_threshold_step=DEFAULT_THRESHOLD / 20,
    )


@router.get("/path-check", response_model=PathCheck)
def path_check(path: str = Query("")):
    """Does this folder exist? The browser can't see the filesystem, so the Config page asks the server."""
    folder = Path(path)
    return PathCheck(path=path, exists=folder.exists(), is_dir=folder.is_dir())
