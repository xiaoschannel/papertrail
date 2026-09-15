"""Config read/write — thin wrapper over settings.get_config/save_config/update_config."""

from fastapi import APIRouter
from pydantic import ConfigDict, create_model

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
