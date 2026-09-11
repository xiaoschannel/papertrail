"""Shared pytest fixtures for the characterization suite.

The committed fixtures under ``tests/fixtures/`` are copied into a per-test
``tmp_path`` so write/move tests never mutate the committed data. Tests that go
through the global config (brand registry, viz aggregation) use
``configured_archive``, which points ``settings.CONFIG_PATH`` at a temp config
whose ``batch_output_path`` is the copied archive.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# Make the project root importable (modules live at the repo root, not a package).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import settings  # noqa: E402
from settings import AppConfig  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def archive_dir(tmp_path: Path) -> Path:
    """A fresh, writable copy of the archived-state fixture."""
    dst = tmp_path / "archive"
    shutil.copytree(FIXTURES / "archive", dst)
    return dst


@pytest.fixture
def ingest_dir(tmp_path: Path) -> Path:
    """A fresh, writable copy of the mid-ingest fixture."""
    dst = tmp_path / "ingest"
    shutil.copytree(FIXTURES / "ingest", dst)
    return dst


@pytest.fixture
def configured_archive(archive_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``archive_dir`` plus a temp ``config.json`` wired through ``get_config()``.

    ``settings.get_config`` resolves ``settings.CONFIG_PATH`` at call time, so
    patching that global redirects every caller (brand registry, viz_data, ...)
    at the archive copy regardless of how they imported ``get_config``.
    """
    cfg = AppConfig(
        batch_output_path=str(archive_dir),
        input_image_path=str(tmp_path / "scans"),
        normalize_engine="string",
        indexing_scheme="Canon ImageFormula",
    )
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg.model_dump(), indent=2), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", cfg_path)
    return archive_dir


@pytest.fixture
def api_client(configured_archive: Path):
    """FastAPI TestClient wired to the fixture archive via patched config."""
    from fastapi.testclient import TestClient

    from api.main import create_app

    return TestClient(create_app())
