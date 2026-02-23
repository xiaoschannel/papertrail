import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

DEFAULTS = {"input_image_path": "", "batch_output_path": ""}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def get_config() -> dict:
    if not CONFIG_PATH.exists():
        return DEFAULTS.copy()
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {**DEFAULTS, **data}


def save_config(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
