"""Config read/write — thin wrapper over settings.get_config/save_config."""

from fastapi import APIRouter

from settings import AppConfig, get_config, save_config

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def read_config() -> AppConfig:
    return get_config()


@router.put("")
def write_config(cfg: AppConfig) -> AppConfig:
    save_config(cfg)
    return get_config()
