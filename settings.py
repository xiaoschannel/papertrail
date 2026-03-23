import json
from pathlib import Path

from pydantic import BaseModel

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class AppConfig(BaseModel):
    input_image_path: str = ""
    batch_output_path: str = ""
    extract_structured: bool = True
    ocr_model: str = ""
    workshop_ocr_model: str = ""
    extractor_model: str = ""
    workshop_extractor_model: str = ""
    parse_custom_instruction: str = ""
    normalize_engine: str = "embedding"
    normalize_embedding_threshold: float = 0.05
    normalize_string_similarity: int = 80
    indexing_scheme: str = ""
    dashboard_rank_by: str = "Total Spend"


def get_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return AppConfig.model_validate({**AppConfig().model_dump(), **data})


def save_config(cfg: AppConfig) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg.model_dump(), indent=2), encoding="utf-8")


def update_config(**kwargs) -> None:
    cfg = get_config()
    for k, v in kwargs.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    save_config(cfg)
