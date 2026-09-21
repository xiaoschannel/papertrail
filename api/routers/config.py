"""Config read/write — thin wrapper over settings.get_config/save_config/update_config."""

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from api import ingest_registry as registry
from api.schemas import ConfigOptions, NormalizeEngineOut, PathCheck
from indexing_schemes import SCHEMES
from name_similarity import DEFAULT_THRESHOLD
from normalize_engines import ENGINES
from settings import NAMED_KEYS, AppConfig, get_config, save_config, update_config

router = APIRouter(prefix="/api/config", tags=["config"])

def _partial(model: type[BaseModel], name: str) -> type[BaseModel]:
    """`model` with every field optional, nested sections included, and nothing else accepted: a PATCH
    changes only what it sends, down to one shortcut. The values are checked where they land
    (update_config validates the whole config), so a clash is judged against the keys in use."""
    def optional(annotation):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return _partial(annotation, f"{annotation.__name__}Patch")
        return annotation
    return create_model(name, __config__=ConfigDict(extra="forbid"),
                        **{f: (optional(field.annotation) | None, None) for f, field in model.model_fields.items()})


AppConfigPatch = _partial(AppConfig, "AppConfigPatch")


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
    try:
        update_config(**patch.model_dump(exclude_unset=True))
    except ValidationError as error:
        raise RequestValidationError(error.errors(include_url=False, include_context=False)) from error
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
        shortcut_named_keys=list(NAMED_KEYS),
    )


@router.get("/path-check", response_model=PathCheck)
def path_check(path: str = Query("")):
    """Does this folder exist? The browser can't see the filesystem, so the Config page asks the server."""
    folder = Path(path)
    return PathCheck(path=path, exists=folder.exists(), is_dir=folder.is_dir())
