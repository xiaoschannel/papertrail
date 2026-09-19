import json
import threading
import time
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
    prefix_suggestion_boundary_only: bool = True
    prefix_suggestion_max_length: int = 24
    prefix_suggestion_min_length: int = 3
    prefix_suggestion_min_count: int = 2
    calendar_period: str = "week"
    calendar_date: str = ""


def get_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    # Windows refuses to open the file for the instant a save swaps the new one in: try again then.
    for attempt in range(50):
        try:
            text = CONFIG_PATH.read_text(encoding="utf-8")
            break
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.01)
    data = json.loads(text)
    return AppConfig.model_validate({**AppConfig().model_dump(), **data})


#: One change to the config at a time, so two changes made together can't undo each other.
_changing = threading.RLock()


def save_config(cfg: AppConfig) -> None:
    """Write the whole config; a reader sees the old file or the new one, never half of one."""
    from data import atomic_write_text

    with _changing:
        atomic_write_text(CONFIG_PATH, json.dumps(cfg.model_dump(), indent=2))


def update_config(**kwargs) -> None:
    """Change only these settings (and write nothing when they already hold these values)."""
    with _changing:
        cfg = get_config()
        before = cfg.model_dump()
        for k, v in kwargs.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        if cfg.model_dump() != before:
            save_config(cfg)
