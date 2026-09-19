"""Shared FastAPI dependencies."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from settings import get_config


def get_output_path() -> Path:
    """Resolved archive (batch_output_path); 400 if unconfigured."""
    cfg = get_config()
    if not cfg.batch_output_path:
        raise HTTPException(status_code=400, detail="Set the batch output path in Config first.")
    return Path(cfg.batch_output_path)


def get_input_path() -> Path:
    """Resolved scan-input directory; 400 if unconfigured."""
    cfg = get_config()
    if not cfg.input_image_path:
        raise HTTPException(status_code=400, detail="Set the input image path in Config first.")
    return Path(cfg.input_image_path)
