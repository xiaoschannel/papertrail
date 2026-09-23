"""Shared pytest fixtures for the characterization suite.

The committed fixtures under ``tests/fixtures/`` are copied into a per-test
``tmp_path`` so write/move tests never mutate the committed data. Tests that go
through the global config (brand registry, viz aggregation) use
``configured_archive``, which points ``settings.CONFIG_PATH`` at a temp config
whose ``root_path`` is the ``tmp_path`` holding the copied archive (``archive/``) and the scan folder
(``scans/``); the folder's history (archive_history) is made there by the first milestone a test hits.
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


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from an empty config of its own, never the machine's config.json.

    Code that reads the config (the brand registry, viz records, the API's archive path) then sees
    defaults unless a fixture such as ``configured_archive`` points it at a fixture copy. The same goes
    for the Experiment bench's scratch folder, the Workshop's rereads held in memory, and what a run
    believes about the rate limit -- a test that is refused a call must not slow the next one down.
    """
    import experiment_runs
    import extraction
    import workshop
    from rate_budget import RateBudget

    scratch = tmp_path_factory.mktemp("isolated")
    monkeypatch.setattr(settings, "CONFIG_PATH", scratch / "config.json")
    monkeypatch.setattr(experiment_runs, "ROOT", scratch / "experiment")
    monkeypatch.setattr(workshop, "rereads", workshop.Rereads())
    monkeypatch.setattr(extraction, "budget", RateBudget())


@pytest.fixture(autouse=True)
def no_leftover_job(monkeypatch: pytest.MonkeyPatch):
    """Stop any background job a test left running, before its fakes and config are undone.

    Requesting ``monkeypatch`` makes this tear down first, while the test's patches still hold, so a
    job still running never sees the real config or the real model registry.
    """
    yield
    from api.jobs import runner
    from api.model_manager import models

    for job in runner.running_jobs():
        runner.cancel(job["id"])
        runner.wait_until_finished(job["id"], timeout=10)
    models.release()


@pytest.fixture
def archive_dir(tmp_path: Path) -> Path:
    """A fresh, writable copy of the archived-state fixture."""
    dst = tmp_path / "archive"
    shutil.copytree(FIXTURES / "archive", dst)
    return dst


@pytest.fixture
def ingest_dir(tmp_path: Path) -> Path:
    """A fresh, writable copy of the mid-ingest fixture."""
    dst = tmp_path / "archive"
    shutil.copytree(FIXTURES / "ingest", dst)
    return dst


@pytest.fixture
def configured_archive(archive_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``archive_dir`` plus a temp ``config.json`` wired through ``get_config()``.

    ``settings.get_config`` resolves ``settings.CONFIG_PATH`` at call time, so
    patching that global redirects every caller (brand registry, viz records, the API, ...)
    at the archive copy regardless of how they imported ``get_config``.
    """
    cfg = AppConfig(
        root_path=str(tmp_path),            # the archive is tmp_path/archive; the scan folder tmp_path/scans
        normalize_engine="string",
        indexing_scheme="Canon ImageFormula",
    )
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg.model_dump(), indent=2), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", cfg_path)
    return archive_dir


@pytest.fixture
def configured_ingest(ingest_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``ingest_dir`` wired through ``get_config()``, with a scan-input folder holding page 1:1's image."""
    scans = tmp_path / "scans"
    scans.mkdir()
    shutil.copy(FIXTURES / "archive" / "tossed" / "08102025143000_201.png", scans / "01102025132642_1.png")
    cfg = AppConfig(
        root_path=str(tmp_path),
        normalize_engine="string",
        indexing_scheme="Canon ImageFormula",
    )
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg.model_dump(), indent=2), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", cfg_path)
    return ingest_dir


@pytest.fixture
def ingest_client(configured_ingest: Path):
    """FastAPI TestClient wired to the mid-ingest fixture."""
    from fastapi.testclient import TestClient

    from api import ingest_store
    from api.main import create_app

    ingest_store.clear()
    return TestClient(create_app(), base_url="http://127.0.0.1")


@pytest.fixture
def api_client(configured_archive: Path):
    """FastAPI TestClient wired to the fixture archive via patched config."""
    from fastapi.testclient import TestClient

    from api import cache
    from api.main import create_app

    cache.clear()  # module-level cache: never let one test see another's archive
    return TestClient(create_app(), base_url="http://127.0.0.1")
