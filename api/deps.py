"""Shared FastAPI dependencies."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from settings import archive_path, get_config, scans_path


def get_root() -> Path:
    """The Papertrail folder (root_path); 400 if unconfigured."""
    cfg = get_config()
    if not cfg.root_path:
        raise HTTPException(status_code=400, detail="Set the Papertrail folder in Config first.")
    return Path(cfg.root_path)


def get_output_path() -> Path:
    """The archive, ``<root>/archive``; 400 if unconfigured."""
    return archive_path(get_root())


def get_input_path() -> Path:
    """The scan folder, ``<root>/scans``; 400 if unconfigured."""
    return scans_path(get_root())
